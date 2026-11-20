"""Brazil economic indicators. Run with Streamlit or pass --refresh."""

import argparse
import calendar
import json
import logging
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

import requests

START = date(2015, 1, 1)
BRASILIA = ZoneInfo("America/Sao_Paulo")
SNAPSHOT = Path(__file__).resolve().with_name("snapshot.json")
SIDRA = "https://apisidra.ibge.gov.br/values"
SGS = "https://api.bcb.gov.br/dados/serie"
COMEX = "https://api-comexstat.mdic.gov.br"

SIDRA_QUERIES = {
    "gdp": ("/t/5932/n1/1/v/6561/p/all/c11255/90707", "6561"),
    "ipca": ("/t/1737/n1/1/v/2265/p/all", "2265"),
    "unemployment": ("/t/6381/n1/1/v/4099/p/all", "4099"),
}
BCB_CODES = {"selic": 432, "fx": 1}


@dataclass(frozen=True)
class Indicator:
    title: str
    unit: str
    frequency: str
    source: str
    url: str
    description: str
    color: str
    months: int = 1
    decimals: int = 2


INDICATORS = {
    "gdp": Indicator(
        "Real GDP growth", "% year over year", "Quarterly",
        "IBGE · SIDRA 5932", "https://sidra.ibge.gov.br/tabela/5932",
        "Official change in real total GDP at market prices from the same quarter a year earlier.",
        "#087D65", months=3, decimals=1,
    ),
    "ipca": Indicator(
        "Consumer inflation (IPCA)", "% over 12 months", "Monthly",
        "IBGE · SIDRA 1737", "https://sidra.ibge.gov.br/tabela/1737",
        "Official headline IPCA change over the previous 12 months.",
        "#AA4B28",
    ),
    "selic": Indicator(
        "Selic target rate", "% per year", "Monthly from daily data",
        "Banco Central do Brasil · SGS 432",
        "https://dadosabertos.bcb.gov.br/dataset/432-taxa-de-juros---meta-selic-definida-pelo-copom",
        "Last available daily policy target in each calendar month.",
        "#3853A0",
    ),
    "unemployment": Indicator(
        "Unemployment rate", "% of labor force", "Monthly release",
        "IBGE · SIDRA 6381", "https://sidra.ibge.gov.br/tabela/6381",
        "Share of the national labor force aged 14 and older that is unemployed. "
        "Each rolling three-month period is labeled by its ending month.",
        "#794593", decimals=1,
    ),
    "fx": Indicator(
        "USD/BRL exchange rate", "BRL per US$", "Monthly from business-day data",
        "Banco Central do Brasil · SGS 1",
        "https://dadosabertos.bcb.gov.br/dataset/1-taxa-de-cambio---livre---dolar-americano-venda---diario",
        "Arithmetic mean of the daily PTAX selling quotes. A higher value means a weaker real.",
        "#127481", decimals=4,
    ),
    "trade": Indicator(
        "Goods trade balance", "US$ billions", "Monthly",
        "MDIC · Comex Stat", "https://comexstat.mdic.gov.br/en/home",
        "National goods exports minus imports, both valued FOB across all goods and partners, "
        "divided by 1 billion.",
        "#526724",
    ),
}


def month_start(day):
    return day.replace(day=1)


def add_months(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 + count, 12)
    return date(year, month + 1, 1)


def period_label(period, indicator):
    if indicator.months == 3:
        return f"Q{(period.month - 1) // 3 + 1} {period.year}"
    return f"{calendar.month_abbr[period.month]} {period.year}"


def parse_period(code, quarterly=False):
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"Invalid IBGE period code {code!r}")
    year, part = int(code[:4]), int(code[4:])
    if quarterly:
        if not 1 <= part <= 4:
            raise ValueError(f"Invalid IBGE quarter {code}")
        part = (part - 1) * 3 + 1
    return date(year, part, 1)


def number(raw):
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError(f"Invalid numeric value {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"Non-finite numeric value {raw!r}")
    return value


def fetch(session, url, *, params=None, body=None):
    # Some upstream errors return an HTML page with HTTP 200.
    for attempt in range(2):
        response = session.request(
            "POST" if body is not None else "GET", url,
            params=params, json=body, headers={"Accept": "application/json"}, timeout=(10, 45),
        )
        response.raise_for_status()
        try:
            return response.json()
        except requests.exceptions.JSONDecodeError as exc:
            message = (f"Expected JSON from {response.url}, got HTTP {response.status_code} "
                       f"with content type {response.headers.get('Content-Type', 'unknown')}")
            if attempt == 1:
                raise ValueError(message) from exc
            logging.warning("%s. Retrying once.", message)
            time.sleep(1)


def require_rows(payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError("Expected a nonempty list of source rows")
    return payload


def put_unique(series, period, value):
    if period in series:
        raise ValueError(f"Duplicate source observation for {period}")
    series[period] = value


def load_sidra(session, key):
    path, variable = SIDRA_QUERIES[key]
    payload = require_rows(fetch(session, SIDRA + path))
    if not payload[0].get("D3C", "").endswith("(Código)"):
        raise ValueError("Unexpected SIDRA column header")
    series = {}
    for row in payload[1:]:
        if (row["NC"], row["D1C"], row["D2C"]) != ("1", "1", variable):
            raise ValueError("Unexpected SIDRA geography or variable")
        if key == "gdp" and row["D4C"] != "90707":
            raise ValueError("Unexpected GDP category")
        period = parse_period(row["D3C"], quarterly=key == "gdp")
        raw = row["V"]
        if raw in ("...", "..", "X"):
            value = None
        else:
            if row["MN"] != "%":
                raise ValueError(f"Unexpected SIDRA unit for {period}")
            value = 0.0 if raw in ("-", "0") else number(raw)
        if period >= START:
            put_unique(series, period, value)
    return [{"period": p.isoformat(), "value": series[p]} for p in sorted(series)]


def year_windows(end):
    """Use five calendar years per query, merging a short January remainder."""
    for year in range(START.year, end.year + 1, 5):
        if year > START.year and year == end.year and end.month == 1:
            break
        last = min(date(year + 4, 12, 31), end)
        if end.year == year + 5 and end.month == 1:
            last = end
        yield date(year, 1, 1), last


def load_bcb(session, key, today):
    url = f"{SGS}/bcdata.sgs.{BCB_CODES[key]}/dados"
    daily = {}
    for first, last in year_windows(today):
        rows = require_rows(fetch(session, url, params={
            "formato": "json", "dataInicial": first.strftime("%d/%m/%Y"),
            "dataFinal": last.strftime("%d/%m/%Y"),
        }))
        for row in rows:
            day = datetime.strptime(row["data"], "%d/%m/%Y").date()
            if day.strftime("%d/%m/%Y") != row["data"] or not first <= day <= last:
                raise ValueError(f"Invalid or out-of-window SGS date {row['data']!r}")
            put_unique(daily, day, number(row["valor"]))
    months = {}
    for day in sorted(daily):
        months.setdefault(month_start(day), []).append((day, daily[day]))

    result = []
    for period, observations in months.items():
        last_day, last_value = observations[-1]
        value = last_value if key == "selic" else mean(v for _, v in observations)
        result.append({
            "period": period.isoformat(), "value": value,
            "last_daily_date": last_day.isoformat(),
            "partial": period == month_start(today),
        })
    return result


def comex_data(payload):
    if payload.get("success") is not True:
        raise ValueError(f"Comex Stat query failed: {payload.get('message')}")
    return payload["data"]


def comex_period(row):
    return parse_period(str(row["year"]) + str(row["monthNumber"]).zfill(2))


def load_trade(session, today):
    latest = comex_period(comex_data(fetch(session, f"{COMEX}/general/dates/updated")))
    if not START <= latest <= month_start(today):
        raise ValueError(f"Invalid latest Comex Stat period {latest}")
    flows = {}
    for flow in ("export", "import"):
        values = {}
        for year in range(START.year, latest.year + 1):
            first = date(year, 1, 1)
            last = min(date(year, 12, 1), latest)
            data = comex_data(fetch(session, f"{COMEX}/general", params={"language": "en"}, body={
                "flow": flow, "monthDetail": True,
                "period": {"from": first.strftime("%Y-%m"), "to": last.strftime("%Y-%m")},
                "filters": [], "details": [], "metrics": ["metricFOB"],
            }))
            for row in require_rows(data["list"]):
                period = comex_period(row)
                raw = str(row["metricFOB"])
                if not first <= period <= last or not re.fullmatch(r"[0-9]+", raw):
                    raise ValueError(f"Invalid {flow} period or USD FOB total: {row!r}")
                put_unique(values, period, int(raw))
        flows[flow] = values
    exports, imports = flows["export"], flows["import"]
    return [
        {"period": p.isoformat(),
         "value": (exports[p] - imports[p]) / 1_000_000_000 if p in exports and p in imports else None}
        for p in sorted(exports.keys() | imports.keys())
    ]


def validate_series(key, rows, today):
    indicator = INDICATORS[key]
    latest = None
    previous = None
    for row in require_rows(rows):
        period = date.fromisoformat(row["period"])
        if (period.isoformat() != row["period"] or not START <= period <= month_start(today)
                or period.day != 1 or (period.month - 1) % indicator.months
                or (previous is not None and period <= previous)):
            raise ValueError(f"Invalid, unordered or duplicate observation date {period}")
        previous = period
        if row["value"] is not None:
            if not isinstance(row["value"], (int, float)):
                raise ValueError(f"Expected JSON number for {period}")
            number(row["value"])
            latest = period
        if key in BCB_CODES:
            day = date.fromisoformat(row["last_daily_date"])
            if (day.isoformat() != row["last_daily_date"] or month_start(day) != period or day > today
                    or row["partial"] is not (period == month_start(today))):
                raise ValueError(f"Invalid daily coverage for {period}")
    if latest is None:
        raise ValueError("Series has no numeric observations")
    return latest


def write_snapshot(snapshot):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=SNAPSHOT.parent, prefix=".snapshot-", delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(snapshot, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, SNAPSHOT)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def refresh():
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    today = datetime.now(BRASILIA).date()
    series = {}
    latest = {}
    stage = "HTTP setup"
    try:
        with requests.Session() as session:
            session.mount("https://", HTTPAdapter(max_retries=Retry(
                total=2, backoff_factor=1, backoff_max=10,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET", "POST"),
            )))
            for key in INDICATORS:
                stage = INDICATORS[key].title
                if key in SIDRA_QUERIES:
                    series[key] = load_sidra(session, key)
                elif key in BCB_CODES:
                    series[key] = load_bcb(session, key, today)
                else:
                    series[key] = load_trade(session, today)
                latest[key] = validate_series(key, series[key], today)
        stage = "snapshot write"
        completed = datetime.now(timezone.utc)
        write_snapshot({"refreshed_at": completed.isoformat(), "as_of_date": today.isoformat(), "series": series})
    except Exception:
        logging.exception("Refresh failed during %s. Previous snapshot preserved.", stage)
        return 1

    for key, indicator in INDICATORS.items():
        count = sum(row["value"] is not None for row in series[key])
        logging.info("%s | %s | %d numeric rows (%d stored) | latest %s",
                     completed.isoformat(), indicator.title, count, len(series[key]),
                     period_label(latest[key], indicator))
    return 0


def read_snapshot():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    refreshed = datetime.fromisoformat(snapshot["refreshed_at"])
    if refreshed.utcoffset() is None:
        raise ValueError("Refresh timestamp must include its UTC offset")
    today = date.fromisoformat(snapshot["as_of_date"])
    latest = {key: validate_series(key, snapshot["series"][key], today) for key in INDICATORS}
    return snapshot["series"], latest, refreshed.astimezone(BRASILIA)


def plot_data(rows, indicator, first, last, today):
    by_period = {date.fromisoformat(row["period"]): row for row in rows}
    periods, values, labels = [], [], []
    offset = (-(first.month - 1)) % indicator.months
    period = add_months(first, offset)
    while period <= last:
        row = by_period.get(period, {})
        label = period_label(period, indicator)
        if row.get("last_daily_date"):
            if row["partial"] or period == month_start(today):
                label += (" · Partial month" if period == month_start(today)
                          else " · Partial at last refresh")
            label += f"<br>Last daily observation {row['last_daily_date']}"
        periods.append(period)
        values.append(row.get("value"))
        labels.append(label)
        period = add_months(period, indicator.months)
    return periods, values, labels


def render_chart(st, go, key, rows, latest, first, last, today):
    indicator = INDICATORS[key]
    row = next(row for row in reversed(rows) if row["value"] is not None)
    observation = period_label(latest, indicator)
    st.subheader(indicator.title)
    st.metric(f"Latest available · {observation}", f"{row['value']:,.{indicator.decimals}f} {indicator.unit}")
    if key in BCB_CODES:
        partial = ""
        if latest == month_start(today):
            partial = "Partial month · "
        elif row["partial"]:
            partial = "Partial at last refresh · "
        st.caption(f"{partial}Last daily observation {row['last_daily_date']}")

    dates, values, labels = plot_data(rows, indicator, first, last, today)
    fig = go.Figure(go.Scatter(
        x=dates, y=values, text=labels, mode="lines+markers", connectgaps=False,
        line={"color": indicator.color, "width": 2.5}, marker={"size": 4},
        hovertemplate=f"%{{text}}<br>%{{y:,.{indicator.decimals}f}} {indicator.unit}<extra></extra>",
    ))
    ticks = dates[::max(1, math.ceil(len(dates) / 6))]
    fig.update_xaxes(tickmode="array", tickvals=ticks,
                     ticktext=[period_label(p, indicator) for p in ticks], type="date")
    fig.update_yaxes(title_text=indicator.unit, tickformat=",.2f", zeroline=key in ("gdp", "trade"))
    fig.update_layout(height=265, margin={"l": 4, "r": 12, "b": 8, "t": 8})
    st.plotly_chart(fig, width="stretch", key=key, config={"displayModeBar": False})
    if not any(value is not None for value in values):
        st.info("No published observations in the selected range.")
    st.caption(f"{indicator.frequency} · [{indicator.source}]({indicator.url})")
    st.caption(indicator.description)


def dashboard():
    import plotly.graph_objects as go
    import streamlit as st

    st.set_page_config(page_title="Brazil Economy Monitor", page_icon="🇧🇷", layout="wide")
    st.title("Brazil Economy Monitor")
    try:
        series, latest, refreshed = read_snapshot()
    except FileNotFoundError:
        st.info("No data snapshot yet. Run `python app.py --refresh` from the repository root, "
                "or run the refresh workflow in GitHub Actions and commit the generated snapshot "
                "to the branch used by Streamlit Community Cloud.")
        return
    except (ValueError, KeyError, TypeError, OSError) as exc:
        st.error(f"The data snapshot could not be read. Run a successful refresh to rebuild it. {exc}")
        return

    st.markdown(f"**Last successful refresh** · {refreshed:%d %b %Y, %H:%M:%S %Z} (Brasília time)")
    gdp = next(row["value"] for row in reversed(series["gdp"]) if row["value"] is not None)
    direction = "grew" if gdp > 0 else "contracted" if gdp < 0 else "was unchanged"
    st.markdown(
        f"Real GDP **{direction}** in **{period_label(latest['gdp'], INDICATORS['gdp'])}** "
        f"compared with the same quarter a year earlier (**{gdp:+.1f}%**)."
    )
    final_period = add_months(max(latest.values()), 1) - timedelta(days=1)
    selected = st.date_input(
        "Observation date range", value=(START, final_period), min_value=START, max_value=final_period,
        help="GDP quarters are placed in their starting month. Other observations use their calendar "
             "month or the ending month of the rolling unemployment period. The range uses whole months.",
    )
    if len(selected) != 2:
        st.info("Select both a start and an end date.")
        return
    first, last = (month_start(day) for day in selected)
    today = datetime.now(BRASILIA).date()

    for pair in (("gdp", "ipca"), ("selic", "unemployment"), ("fx", "trade")):
        for column, key in zip(st.columns(2), pair):
            with column:
                with st.container(border=True):
                    render_chart(st, go, key, series[key], latest[key], first, last, today)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Build and replace the local snapshot")
    if parser.parse_args().refresh:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        raise SystemExit(refresh())
    dashboard()
