# Brazil Economy Monitor

An English-language Streamlit dashboard tracking whether Brazil’s real GDP is growing and how inflation, interest rates, unemployment, the exchange rate, and goods trade are evolving alongside it. The six charts default to January 2015 through the latest observation in the snapshot. The date range filters the charts, while the headline and latest-value cards continue to show the latest published figures in the snapshot.

## Local setup and first refresh

Install Python 3.12 and run these commands from the repository root. On Windows, activate the environment with `.venv\Scripts\Activate.ps1` in PowerShell.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py --refresh
streamlit run app.py
```

The initial files are `app.py`, `requirements.txt`, and `README.md`. A successful first refresh creates `snapshot.json` next to `app.py`. The dashboard shows setup instructions while that file is absent. Each refresh checks the dates, numeric values, and presence of observations for all six series. The completed refresh timestamp and all observations are written together with an atomic file replacement. Any download, validation, or write error leaves the prior snapshot in place and exits with status 1. The CLI logs a UTC completion timestamp, numeric and stored row counts, and the latest observation period for each series. The dashboard reads the persisted timestamp and displays it in Brasília time (`America/Sao_Paulo`).

## Official metadata and exact queries

### IBGE SIDRA

The metadata confirms national territorial level `N1`, selected as `n1/1` (Brazil) in all three requests. The requests select one variable each, and GDP also selects one classification category. The SIDRA API limit is 100,000 values per request. All three requests are well below it. The app validates the returned geographic, variable, classification (GDP), and numeric unit fields and keeps periods from January 2015 onward. SIDRA’s unavailable, inapplicable, and suppressed markers become gaps. Its zero markers are preserved as zero. [SIDRA API help and limits](https://apisidra.ibge.gov.br/home/ajuda)

| Indicator | Confirmed definition and period code | Official metadata |
| --- | --- | --- |
| Real GDP growth | Table 5932 variable `6561`, category `11255/90707` (total GDP at market prices). Quarterly volume change versus the same quarter a year earlier, in percent. `YYYY01` through `YYYY04` identify Q1 through Q4. | [Table](https://sidra.ibge.gov.br/tabela/5932) · [Variables](https://servicodados.ibge.gov.br/api/v3/agregados/5932/metadados) · [Periods](https://servicodados.ibge.gov.br/api/v3/agregados/5932/periodos) |
| Consumer inflation | Table 1737 variable `2265`. Nationwide headline IPCA cumulative change over 12 months, in percent. `YYYYMM` identifies the calendar month. | [Table](https://sidra.ibge.gov.br/tabela/1737) · [Variables](https://servicodados.ibge.gov.br/api/v3/agregados/1737/metadados) · [Periods](https://servicodados.ibge.gov.br/api/v3/agregados/1737/periodos) |
| Unemployment | Table 6381 variable `4099`. Unemployed people as a percentage of the national labor force aged 14 and older. `YYYYMM` is the ending month of the rolling three-month period, released monthly. For example, `202609` covers July through September 2026. | [Table](https://sidra.ibge.gov.br/tabela/6381) · [Variables](https://servicodados.ibge.gov.br/api/v3/agregados/6381/metadados) · [Periods](https://servicodados.ibge.gov.br/api/v3/agregados/6381/periodos) |

Exact GET requests

```text
https://apisidra.ibge.gov.br/values/t/5932/n1/1/v/6561/p/all/c11255/90707
https://apisidra.ibge.gov.br/values/t/1737/n1/1/v/2265/p/all
https://apisidra.ibge.gov.br/values/t/6381/n1/1/v/4099/p/all
```

### Banco Central do Brasil SGS

- **Selic target rate (432).** Brazil’s national monetary policy target set by Copom, in annualized percent. The chart uses the last available daily target within each calendar month. [BCB metadata](https://dadosabertos.bcb.gov.br/dataset/432-taxa-de-juros---meta-selic-definida-pelo-copom)
- **USD/BRL (1).** Official Brazilian real per US dollar free-market selling rate (daily closing PTAX). The chart uses the arithmetic mean of the available business-day quotes within each calendar month. A higher value means a weaker real against the dollar. [BCB metadata](https://dadosabertos.bcb.gov.br/dataset/1-taxa-de-cambio---livre---dolar-americano-venda---diario)

BCB requires date filters for daily requests and limits each range to 10 years. The app starts at 1 January 2015 and requests consecutive five-calendar-year windows, ending at the Brasília date at refresh start. An ending range limited to January at a five-year boundary is included in the preceding window so that a holiday at the start of the new window cannot cause an empty request. Request and response dates use `DD/MM/YYYY`. The current month is partial and the dashboard shows its last underlying date. A previously partial month remains labeled “Partial at last refresh” if the snapshot predates the calendar rollover.

Exact GET templates (slashes in dates are URL-encoded by the HTTP client)

```text
https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json&dataInicial={DD%2FMM%2FYYYY}&dataFinal={DD%2FMM%2FYYYY}
https://api.bcb.gov.br/dados/serie/bcdata.sgs.1/dados?formato=json&dataInicial={DD%2FMM%2FYYYY}&dataFinal={DD%2FMM%2FYYYY}
```

For example, the first Selic request is

```text
https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados?formato=json&dataInicial=01%2F01%2F2015&dataFinal=31%2F12%2F2019
```

### MDIC Comex Stat

The general trade API supplies national goods totals in US dollars FOB for both export and import flows. No product, geography or partner filters or breakdowns are applied. The app joins the two flows by calendar month and calculates `(exports - imports) / 1_000_000_000`. A missing flow leaves a gap. The OpenAPI specifies the same row and execution limits as the Comex Stat interface without giving numeric limits for this endpoint. The app requests one calendar year at a time with at most 12 monthly totals per flow per request. This also avoids omissions observed when a request spanned several years and ended before December. [Comex Stat](https://comexstat.mdic.gov.br/en/home) · [API documentation](https://api-comexstat.mdic.gov.br/docs) · [OpenAPI schema](https://api-comexstat.mdic.gov.br/docs/doc.yaml) · [FOB metric metadata](https://api-comexstat.mdic.gov.br/general/metrics?language=en)

```text
GET https://api-comexstat.mdic.gov.br/general/dates/updated
POST https://api-comexstat.mdic.gov.br/general?language=en
Content-Type: application/json
```

The app issues the following body for exports and repeats it with `"flow": "import"`. It iterates from 2015 through the latest published year returned by `/general/dates/updated`. The latest request uses that endpoint’s `year` and `monthNumber` for the upper bound, so it ends at the latest published month. The request period uses `YYYY-MM`, and each response row supplies `year` and `monthNumber`.

```json
{
  "flow": "export",
  "monthDetail": true,
  "period": {"from": "2015-01", "to": "2015-12"},
  "filters": [],
  "details": [],
  "metrics": ["metricFOB"]
}
```

## GitHub Actions and Streamlit Community Cloud

1. Add the three delivered files to the repository root on the default branch. When enabling daily refresh, copy the workflow below into `.github/workflows/refresh.yml` and commit it. The workflow runs daily at 12:17 UTC (09:17 Brasília time) and is also available under **Actions → Refresh Brazil economy data → Run workflow**. It commits the generated snapshot to the default branch. The repository must permit the workflow token to push to that branch. If branch protection requires pull requests, direct snapshot pushes need an authorized exception or a separately configured merge process.
2. Run the workflow manually for the first snapshot, or generate it locally with `python app.py --refresh` and commit and push `snapshot.json`. A new snapshot carries a new timestamp even when the series values are unchanged.
3. In Streamlit Community Cloud, create an app from the GitHub repository, select its default branch, set `app.py` as the main file and choose **Python 3.12** in Advanced settings. Deploy. Community Cloud picks up commits on the linked branch and serves the checked-in snapshot. The app reads that file when it runs.
4. To verify a deployment refresh, compare the successful Actions log and the snapshot commit on the linked branch. Open or reload the deployed dashboard and check that **Last successful refresh** matches the commit’s `snapshot.json` value of `refreshed_at` after converting from UTC to Brasília time. Each chart’s latest period should match the Actions log. Reload once more to confirm the displayed timestamp stays the same. If it still shows an older timestamp, check the configured repository and branch and review Community Cloud’s app logs. [Streamlit deployment guide](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app)

```yaml
name: Refresh Brazil economy data

on:
  schedule:
    - cron: "17 12 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: brazil-economy-snapshot
  cancel-in-progress: false

jobs:
  refresh:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Check out the default branch
        uses: actions/checkout@v4
        with:
          ref: ${{ github.event.repository.default_branch }}

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install requirements
        run: python -m pip install -r requirements.txt

      - name: Refresh all six series
        run: python app.py --refresh

      - name: Commit and push the snapshot
        env:
          DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add -- snapshot.json
          git commit -m "Refresh Brazil economy snapshot"
          git push origin "HEAD:refs/heads/${DEFAULT_BRANCH}"
```

## Build prompt

> Build an English-language Streamlit dashboard for Brazil that answers whether real GDP is growing and how consumer inflation, interest rates, and the labor market are evolving alongside it. Use Python 3.12, GitHub for version control and scheduled refresh, and Streamlit Community Cloud for hosting. Deliver exactly three initial root files, `app.py`, `requirements.txt`, and `README.md`.
>
> Verify official metadata for national coverage, exact variable, units, and period codes before constructing requests. Use only official machine-readable source data and honor request limits. Chart IBGE SIDRA 5932 real total GDP growth versus the same quarter one year earlier, SIDRA 1737 headline IPCA change over 12 months, SIDRA 6381 unemployment for rolling three-month periods, BCB SGS 432 last daily Selic target per calendar month in annualized percent, BCB SGS 1 arithmetic mean of daily PTAX selling quotes in BRL per US dollar, and MDIC Comex Stat monthly goods exports minus imports across all goods and partners, both FOB, in US$ billions. Record exact requests and document unavailable endpoints and pending checks.
>
> Include one chart per indicator, a date range defaulting to January 2015 through the latest observation, units, frequency, source links, latest periods, and transformation notes. Label quarters and calendar months explicitly and label unemployment by the rolling period’s ending month. Leave gaps for missing observations. Flag current-month Selic and FX observations as partial and show the last underlying daily date. Display the persisted last successful refresh timestamp prominently in Brasília time. Explain setup if no snapshot exists.
>
> Support `streamlit run app.py` and `python app.py --refresh`. Download and validate all six series before atomically replacing the prior snapshot. Fail with a nonzero status and preserve the prior snapshot on error. On success, log the completion time, row count, and latest period for each series. Record a new timestamp on every success, including refreshes with unchanged data. Include a copy-ready daily and manually triggered GitHub Actions workflow, Python 3.12 dependencies, GitHub and Community Cloud setup, instructions to verify the deployed snapshot, this prompt, and verification evidence in the README.

## Verification evidence

A live `python app.py --refresh` using Python 3.12.14 completed successfully on **19 November 2026 at 22:13:00 Brasília time** (`2026-11-20T01:13:00.746481+00:00` in the snapshot and CLI log). The snapshot and working test files were kept outside the three-file handoff.

| Series | Numeric rows | Latest period | Latest value |
| --- | ---: | --- | ---: |
| GDP | 46 | Q2 2026 | 2.0% year over year |
| IPCA | 142 | Oct 2026 | 4.63% over 12 months |
| Selic | 143 | Nov 2026, partial through 19 Nov | 13.75% per year |
| Unemployment | 141 | Sep 2026 | 5.2% |
| USD/BRL | 143 | Nov 2026, partial through 19 Nov | 5.6213 BRL per US$ |
| Goods trade balance | 142 | Oct 2026 | US$9.32 billion |

- Separate official requests reproduced the latest three SIDRA values, the last November Selic target, the mean of 13 November PTAX quotes, and October goods exports of US$37,807,241,261 minus imports of US$28,489,685,220. The broad Comex Stat query returned fewer months than requested. The final calendar-year request strategy returned all 142 months from January 2015 through October 2026.
- The Streamlit server health endpoint returned `ok`. Streamlit AppTest found six charts and six latest-value cards, verified both partial-month labels and last daily dates, checked that date filtering changed the plotted periods, and confirmed the timestamp stayed stable on rerun. It also checked the missing-snapshot setup message. A focused gap check verified that an absent GDP quarter generates an empty chart value between the surrounding observations.
- An injected invalid value in the final series made the refresh return status 1, with the previous snapshot byte-for-byte unchanged. A separate successful refresh with identical series data produced a new timestamp.
- One intermediate request to SGS series 1 returned an HTTP response that could not be parsed as JSON. That run stopped before replacing the snapshot, and the later live run and independent series 1 check passed. All required source checks are complete.
- The workflow YAML was parsed locally and checked for both trigger types, repository write permission, Python 3.12, and the refresh command. GitHub Actions execution and Community Cloud deployment are pending repository setup.
