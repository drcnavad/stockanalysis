# Auto Fetch Fundamentals - Data Fetching Documentation

## Overview

`company_report_autofetch.py` fetches quarterly income statements and balance sheets from **Alpha Vantage** and writes the merged rows to `Reports/balance_sheet.csv`, the input for `company_report_processing.ipynb`.

## Running

```bash
python company_report_autofetch.py             # daily rotation
python company_report_autofetch.py --symbol UPST  # one stock now
python run_all.py --full         # rotation (once per day) + news + the rest of the pipeline
```

- **Daily rotation** (default, `MANUAL_SYMBOL = ""`): up to 12 symbols whose last successful fetch is older than 90 days. Symbols attempted within the cooldown (~10 days) are skipped so the whole list rotates first.
- **Manual single stock**: set `MANUAL_SYMBOL = "UPST"` at the top of the script and run it. Rotation and cooldown are skipped. Add new tickers to `sector_mapping.stock_symbols` so the rotation keeps them fresh.

## Key Configuration

```python
MANUAL_SYMBOL = ""     # "TICKER" = fetch one stock now
STALE_DAYS = 90        # refresh target
MAX_STOCKS = 12        # 25 calls/day free tier, 2 calls per stock
CALL_DELAY_SEC = 13    # 5 calls/min free tier
YEARS_TO_KEEP = 5      # only last 5 years saved
IQR_MULT = 1.5         # per-symbol clipping of RATIO columns only (raw financials are never clipped)
```

Universe = the watchlist in the script + every symbol in `sector_mapping.stock_symbols`.

## Output: `Reports/balance_sheet.csv`

| Column | Notes |
|--------|-------|
| Symbol, FiscalDateEnding | one row per quarter |
| TotalRevenue, GrossProfit, NetIncome, OperatingIncome | millions |
| TotalAssets, TotalLiabilities, TotalShareholderEquity, CommonStockSharesOutstanding | millions |
| TotalDebt, CashAndEquivalents | millions (short + long-term debt; cash) |
| OperatingMargin | % |
| BVPS, Debt_to_Equity (total debt / equity), Liabilities_to_Equity | ratios (computed before scaling) |
| Date, DateAdded | fetch date |

A symbol's rows are replaced only when **both** statements return data; empty responses are logged and alerted, and existing rows stay untouched. Rate-limit replies (`Note` / `Information`) stop the run early.

## Run Log: `Reports/fetch_run_log.csv`

`Symbol | RunDate | IncomeStatus | BalanceStatus` with statuses `ok`, `empty`, `rate_limit`, `skipped`, `error` (network/HTTP failure after 3 retries; the run stops). Missing `balance_sheet.csv` / `fetch_run_log.csv` are created. A fetch counts as successful only when both statuses are `ok`.
