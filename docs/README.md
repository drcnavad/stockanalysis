# Stock Analysis Pipeline

## Quick start: one command

```bash
python run_all.py          # the daily command (or open run_all.ipynb and "Run All")
streamlit run app.py       # the dashboard
python tests/run_tests.py  # checks: exact backtest reproduction, rank audit, runner, app, paper account (mock), 8599 smoke test
                           # (≈25 s; --fast skips the app / dashboard tests)
```

`run_all.py` decides what to run by itself (US Central clock):

| When you run it | Mode | What runs | Quota API calls |
|-----------------|------|-----------|-----------------|
| **Mon / Wed / Fri after 3:15 PM CT** (trading day), first time that day | **full** | Alpha Vantage rotation (max 24 calls), company reports, news sentiment (NewsAPI 97 + Finnhub 97), earnings dates (Finnhub 97), main signal analysis, report checks | ≈24 + 97 + 194 |
| any other time (Tue/Thu, weekends, before 3:15 PM, or a second run the same day) | **quick** | main signal analysis (fresh Alpaca daily bars) + report checks | none |

Guards: NewsAPI never runs twice within 24 h (free limit 100/day, one run = 97) and the Alpha Vantage rotation runs once per date,
even with `--full`; `--force-news` / `--force-fundamentals` override. In full mode, if the main step would start before 4:31 PM ET it
waits (up to 20 min) so the day's final bar is used. State: `Reports/run_state.json`; logs: `Reports/logs/run_*.log` (last 30).
Each run ends with a short summary: what ran / was skipped, API calls used (counted by the notebooks), data date and rules, the
latest decisions, the **ALERT** line, the next decision and when the next full update is due. A quick run takes ≈9 s, a full run
≈1–3 min (network-bound).

```bash
python run_all.py --dry-run              # show the plan (mode, steps, expected API calls); nothing runs
python run_all.py --full | --quick       # force a mode
python run_all.py --full --force-news    # allow a second NewsAPI run (may exceed the free 100/day)
python run_all.py --positions f.csv      # alert on another positions file (default: my_positions.csv if it exists)
python run_all.py --only main | --from scoring | --list | --backtests | --visualization | --keep-going
python run_all.py --sync-paper          # also refresh my_positions.csv from your Alpaca PAPER account (off by default)
```

It never places orders. Alpaca account endpoints are only called with `--sync-paper` (3 read-only GET requests to the
PAPER account); otherwise only Alpaca market-data bars are used. Notebooks run by hand still work;
the sentiment notebook then refuses a second NewsAPI run within 24 h unless `PIPELINE_FORCE_NEWS=1`.

## How to use (live rules C6-U96-T20-MW30-E5)

The strategy makes a decision at three closes a week. Run `python run_all.py` each time **after 3:15 PM CT** (it waits for the
final bar if needed):

| When | What it decides | Trade at |
|------|-----------------|----------|
| **Friday** (the week's last session) after the close | full rebalance: the new top 10 from ranks 1–20 | Monday open |
| **Monday** after the close | mid-week check: swap + rank-30 exit | Tuesday open |
| **Wednesday** after the close | mid-week check: swap + rank-30 exit | Thursday open |
| next morning, before the open: `python paper_trade.py --account-size N --positions my.csv` | dry-run order list (nothing is sent) | — |

Holidays: the rebalance moves to the week's last session (e.g. Thursday before Good Friday); a Monday/Wednesday holiday moves the
check to the next session. The output always names the close and the open it applies to.

**The rules** (`backtest_engine.WINNER`; the ranking uses price bars only, news and fundamentals are shown for context):
1. **Score** = 0.5 × Technical_Score (MA/RSI/MACD/Force Index/OBV/Bollinger/Fibonacci) + 0.5 × relative strength (stock vs its
   sector ETF and sector ETF vs SPY over 21/63/126 days). Rank all 96 stocks by score; only score > 0 qualifies.
2. **Friday:** walk ranks 1–20 with max 4 per sector; if fewer than 10 fit, fill the free slots from the unused ranks 1–20 ignoring
   the sector limit. Nothing worse than rank 20 is bought (fewer qualifying → the rest stays cash). Weights ∝ 1/63-day volatility.
   If QQQ closes at/below its 200-day average, every weight is halved.
3. **Mon/Wed swap:** if a stock you do NOT hold is in the **top 3** and a holding has fallen **below rank 15** (or no longer
   qualifies), sell the worst-ranked holding and buy the new stock with the same dollars (any sector; can repeat).
4. **Mon/Wed exit:** after the swap step, sell any holding ranked **worse than 30**; the cash waits for Friday.
5. **Earnings rule (E5, live from 2026-09-25, user decision, not a tested rule):** at the Friday rebalance and the Mon/Wed checks, a
   stock that is **not held** is not bought when **decision date < next earnings date ≤ decision date + 5 calendar days**
   (dates from `Reports/earnings_date.csv`). Its slot goes to the next eligible stock in ranks 1–20 (same rules as step 2), else cash.
   A top-3 swap candidate with earnings that close is skipped. **Held stocks are never sold because of earnings.** The reason reads
   `earnings in 3 days (Wed Sep 30): not bought` in `strategy_changes.csv` / `strategy_decisions.csv`, the app and the alert.

At the end the run prints e.g. `Mid-week check at the Mon Sep 28 close: SELL X (rank 22) and BUY Y (rank 2) at the Tue Sep 29 open,
same dollar amount.` or `No swap (...)` plus the next decision; the Friday line adds `Not bought (earnings within 5 days): ...` when
the rule skipped a stock. The same lines are in `Reports/strategy_midweek_check.csv` and the app's **This week** box.

**Holdings alert (top of the app, and the ALERT line of `python run_all.py`):** one plain line per action, tickers link to
the app (`http://localhost:8501/?symbol=XXX`), e.g. `Swap at the Tue Sep 29 open: sell X and buy Y (rank 2), same dollar amount.`,
`Sell TRGP (rank 45) at the Tue Sep 29 open, hold cash until Friday.`, `No swap or exit with today's ranks (MU rank 2 not bought:
earnings in 2 days (Wed Sep 30)). Next check: ...` or `Full rebalance at the Mon Sep 28 open: sell ...; buy ...` (Friday).
It uses the strategy's holdings unless **`my_positions.csv`** exists in the project folder; then it checks YOUR holdings with the
same rules (stocks outside the 96 count as not ranked) and says so if this week's check called for a trade your file shows as not
done. Format: `Symbol,Shares` (an optional `Weight` column in % also works). Copy `my_positions.example.csv` to `my_positions.csv`,
or let `python run_all.py --sync-paper` / `alpaca_paper_account.ipynb` write it from your Alpaca paper account. Also:
`python holdings_alert.py [--positions f.csv | --strategy]`.

**Revert a rule** (edit `WINNER` in `backtest_engine.py`, then `python run_all.py`; each step is independent):
- no earnings rule: `WINNER["earnings_block_days"] = None`
- no rank-30 exit: `WINNER["midweek_exit_below"] = None`
- weekly-only: `WINNER["midweek_swap"] = None`
- picks from any rank with the hard max-4 limit: `WINNER["max_pick_rank"] = None` and `WINNER["cap_soft"] = False`
- universe: `sector_mapping.EXPANDED_UNIVERSE = "high_beta_91"` (91 stocks) or `None` (the original 78)

Tracking rows (`strategy_tracking.csv`) keep the rule label they were written with and are chain-linked across rule changes. After
any rule change the decision history is recomputed with the new rules from the start of the window.

## Backtest

`backtest.ipynb` (or `python run_all.py --backtests`) runs the live rules with the same engine as the pipeline on the cached bars
(no API quota): walk-forward 2022-04-01 → latest bar, decisions at the close, next-open fills, 0.1% cost per side. It writes
`Reports/backtest_summary.csv` and `Reports/backtest_per_stock.csv` (shown in the app) and has a short section to compare one rule
variant (`VARIANT = {...}` in its first cell). Results (last 1y / 2y vs QQQ +24.9% / +54.2%, SPY +17.3% / +37.4%):

| Rules | Total | Sharpe | Max DD | Never-seen Sharpe (2022-04 → 2024-09) | Last 1y | Last 2y |
|-------|-------|--------|--------|-------------------|---------|---------|
| C6-U96 weekly only | +390.6% | 1.40 | −32.6% | 0.66 | | |
| C6-U96-MW (`Reports/rebalance_frequency_test.csv`, variant D) | +438.08% | 1.4649 | −31.84% | 0.8414 | +41.8% | +232.7% |
| C6-U96-MW30 (`Reports/sell_rule_test.csv`, S3) | +421.59% | 1.4695 | −32.18% | 0.8172 | +45.3% | +228.9% |
| C6-U96-T20-MW30 (user decision) | +414.07% | 1.3716 | −32.19% | 0.5958 | +54.4% | +267.5% |
| **C6-U96-T20-MW30-E5 (live)**, PARTIAL (see below) | +399.90% | 1.3483 | −30.76% | 0.5958 | +52.8% | +257.3% |

The earnings rule can only use the earnings dates on disk: `Reports/earnings_date.csv` starts at 2024-09-25 and (until the next
online earnings run) has no dates for 18 of the 96 stocks, so the E5 row is **partial, for information only**; before late 2024 it is
identical to T20-MW30. QQQ over the whole walk-forward: +110.1%, Sharpe 0.85. The stock list is hand-picked with hindsight, so expect
weaker live results. `tests/test_midweek_repro.py` confirms the engine reproduces every row above exactly (the tested rows against the
original tests, T20 and E5 against independent re-implementations) and matches the pipeline's decision history.

Research behind the rules (kept as evidence, not needed to run anything): `rebalance_frequency_test.csv`, `sell_rule_test.csv`,
`strategy_comparison_sector_caps.csv` (caps 2–8: not adopted), `strategy_vs_qqq_1y_2y.csv`, `universe_*.csv`
([universe_expansion.md](universe_expansion.md)). Tested and not adopted: rank buffers, score-only exits, minimum holds,
2-weekly/monthly rebalancing, MA50/ATR stops, absolute score thresholds, sector caps other than 4.

## Project layout

- `run_all.py` / `run_all.ipynb`: the one command. `app.py`: Streamlit dashboard.
- `backtest_engine.py`: live rules (`WINNER`), technical indicators, ranking, selection, mid-week and earnings rules, simulator,
  and the backtest helpers used by `backtest.ipynb`.
- `sector_mapping.py`: universe and sectors. `holdings_alert.py`: the alert. `paper_trade.py`: dry-run orders (and optional paper
  submit). `alpaca_paper.py` + `alpaca_paper_account.ipynb`: read-only view of the Alpaca PAPER account.
  `company_report_autofetch.py`: Alpha Vantage fundamentals.
- Notebooks: the pipeline steps below and `backtest.ipynb`.
- `tests/`: `python tests/run_tests.py` (see the file for the list). `Reports/`: every output and `logs/`. `docs/`: this file and
  the per-notebook docs.

## Run order and outputs (all in `Reports/`)

| # | Step | Output | Read by | Quota APIs |
|---|------|--------|---------|------------|
| 1 | `company_report_autofetch.py` (full mode) | `balance_sheet.csv`, `fetch_run_log.csv` | processing | Alpha Vantage |
| 2 | `company_report_processing.ipynb` | `complete_company_analysis.xlsx` | scoring, visualization, app | – |
| 3 | `company_report_scoring.ipynb` | `balance_sheet_weights.csv` | main | – |
| 4 | `sentiment_analysis.ipynb` | `news_cleaned_df.csv`, `weighted_sentiment.csv`, `sentiment_history.csv` | main, app | NewsAPI, Finnhub (online mode) |
| 5 | `earnings_date.ipynb` | `earnings_date.csv` | main (earnings rule), alert, app, backtest | Finnhub (online mode) |
| 6 | `main_signal_analysis.ipynb` | `signal_analysis.csv`, `strategy_picks.csv`, `strategy_changes.csv`, `strategy_holdings.csv`, `strategy_tracking.csv`, `strategy_decisions.csv`, `strategy_midweek_check.csv`, `benchmark_prices.csv`, `factor_history.csv` | app, alert, `paper_trade.py` | – |
| 7 | `company_report_visualization.ipynb` (opt-in) | inline charts (`COMPANY_SYMBOL=NVDA` env picks the stock) | you | – |
| 8 | `backtest.ipynb` (opt-in, `--backtests`) | `backtest_summary.csv`, `backtest_per_stock.csv` | app | – |

`app.py` (Streamlit) is the deployed file; git tracks it together with the reports it reads. The app maintains `daily_rank.csv`.

### Notebook modes (environment variables, set automatically by `run_all.py`)

| Variable | Values | Meaning |
|----------|--------|---------|
| `PIPELINE_SENTIMENT_MODE` | `online` (default by hand) / `offline` / `sample` | offline = re-apply relevance filter + scores to cached news, no network; sample = 1–2 symbols (`PIPELINE_SAMPLE_SYMBOLS`) written only to `Reports/cache/sample_*` |
| `PIPELINE_EARNINGS_MODE` | `online` / `offline` / `sample` | offline = normalize/dedupe the existing file; online merges new dates into the file (past dates are kept) |
| `COMPANY_SYMBOL` | ticker | stock shown by the visualization notebook |

## History of the live rules

- **2026-09-25 E5** (user decision, no pre-test): no new buys with earnings within 5 calendar days. `rules_version` `v4-mw30-t20-e5`.
- **2026-09-25 T20** (user decision, no pre-test): picks only from ranks 1–20, sector limit relaxed to fill 10 slots.
- **2026-09-25 MW30** (user decision): rank-30 mid-week exit (sell-rule test S3; missed the pre-declared switch rule only on the
  never-seen period, 0.82 vs 0.84).
- **2026-09-24 MW** (user decision): Mon/Wed "top 3 in, below 15 out" swap (rebalance-frequency test variant D: +438%, 1.46,
  never-seen 0.84 vs weekly-only +391%, 1.40, 0.66; full Mon/Wed/Fri or daily rebalancing did worse after costs).
- **2026-09-24 U96** (user decision): + CRDO NBIS LITE CLS RBRK (Technology). Hindsight caveat: picked on that day's news after big
  run-ups; the backtest still did not improve. NBIS bars before 2024-10-21 are dropped (`sector_mapping.HISTORY_START`); new
  listings rank after 200 bars.
- **2026-09-24 U91** (user decision): + 13 names with 2019–21 beta ≥ 1.5 (APA OXY TRGP DVN FANG COF C BE BA URI PH FCX LYB). It
  failed the pre-declared never-seen test (Sharpe 0.72 vs 0.92 for the original 78).
- **C6** (base rules): weekly top 10, max 4 per sector, soft QQQ regime, chosen with a rolling quarterly walk-forward.
  The online news step makes ≈97 NewsAPI calls (96 stocks + QQQ; free limit 100/day) + ≈97 Finnhub.

## Paper trading

`python paper_trade.py --account-size 25000 [--positions current.csv]` prints the orders needed to reach the target weights
(dry run, nothing sent). After a Mon/Wed check with a swap, `auto` mode prints only the swap: SELL all shares of the stock that fell
below rank 15 and BUY the new top-3 stock for the same dollars (`--target midweek` forces it; without `--positions` the dollars =
the old stock's target weight × account size). Only `python paper_trade.py --submit --paper` sends DAY market orders, and only to the Alpaca **paper**
account; live trading is refused. The app has the same order preview (Details → Order preview).

## Paper account (read-only)

Add your Alpaca **paper** keys to `.env` (names in `.env.example`; values from Alpaca → Paper Trading → API keys):

```
ALPACA_PAPER_KEY_ID=...
ALPACA_PAPER_SECRET_KEY=...
```

(`APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` also work, and so does `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` when that key ID starts with
`PK`, i.e. is a paper key. A live key (`AK...`) is never used for the paper account.)

Then open `alpaca_paper_account.ipynb` and Run All, or use the command line:

```bash
python alpaca_paper.py            # account summary (equity, cash, buying power), positions, recent orders
python alpaca_paper.py --sync     # ... and write my_positions.csv + Reports/paper_portfolio_snapshot.csv / paper_account_history.csv
python run_all.py --sync-paper    # the daily run, refreshing my_positions.csv before the ALERT line
```

Only `https://paper-api.alpaca.markets/v2` is accepted (any other URL is refused before a request is made), only GET requests
to /account, /positions and /orders exist in the code, and the keys are never printed or written to a file.
`my_positions.csv` (Symbol,Shares) is what the app's top alert and `holdings_alert.py` compare with the strategy.

## Dashboard (`streamlit run app.py`, open http://localhost:8501; `?symbol=NVDA` opens a stock directly)

- **Top:** title bar (last update, stale-file warning) and ONE alert line saying what to do today.
- **Dashboard tab:** summary: market filter, invested %, stocks held, last / next decision; the **This week** box (Friday rebalance
  + Mon/Wed checks); one table of the current picks next to the preview (Signal, rank, Portfolio weight %, Preview weight %,
  sector, next earnings, why; click a row to open the stock); earnings in the next 7 days. Below it, the **stock view**: picker +
  AI analysis button, header (price, Bullish / Hold / Bearish signal, score, rank, portfolio slot and weight, P&L if held), the
  chart (price with entries ▲ / exits ▼, held periods shaded, market-filter weeks ◆, 3×ATR stop reference, next earnings;
  optional score & rank, relative strength, RSI/MACD, legacy signals) and a **More about** expander (moving averages,
  fundamentals & news context, chart guide, per-stock backtest).
- **Details tab** (expanders): all signals (official / preview, filter, rank change), rank history (20 sessions), rules and the
  latest decisions with reasons, holdings risk and forward tracking vs QQQ/SPY, order preview (nothing is sent), backtest results
  (medians), data freshness and the alert-source setting.

## Documentation Index

| Doc | Description |
|-----|-------------|
| [universe_expansion.md](universe_expansion.md) | Universe expansion tests, results, the live C6-U96 switch and how to revert |
| [company_report_autofetch.md](company_report_autofetch.md) | Alpha Vantage rotation + manual symbol, 5 years, retries, ratio-only outlier clipping |
| [company_report_processing.md](company_report_processing.md) | Financial ratios, TTM, fair value |
| [company_report_scoring.md](company_report_scoring.md) | Fundamental weight scoring (-10 to +10) |
| [company_report_visualization.md](company_report_visualization.md) | Plotly charts for symbol analysis |
