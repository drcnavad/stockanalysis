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

## How to use (live rules C6-U96-T20-MW30)

The strategy makes a decision at three closes a week. Run `python run_all.py` each time **after 3:15 PM CT** (it waits for the
final bar if needed):

| When | What it decides | Trade at |
|------|-----------------|----------|
| **Friday** (the week's last session) after the close | full rebalance: the new top 10 from ranks 1–20 (max 4 per sector, relaxed to fill 10) | Monday open |
| **Monday** after the close | mid-week check: swap + rank-30 exit | Tuesday open |
| **Wednesday** after the close | mid-week check: swap + rank-30 exit | Thursday open |
| next morning, before the open: `python paper_trade.py --account-size N --positions my.csv` | dry-run order list (nothing is sent) | — |

Holidays: the rebalance moves to the week's last session (e.g. Thursday before Good Friday); a Monday/Wednesday holiday moves the
check to the next session (e.g. Tuesday after MLK day) — the output always names the close and the open it applies to.

**Friday selection (T20, live from 2026-09-25, user decision):** only stocks ranked **1–20** can be bought. Walk ranks 1..20 with the
max-4-per-sector limit; if fewer than 10 fit, fill the free slots from the unused ranks 1..20 in rank order **ignoring the sector
limit** (a 5th or 6th Tech stock is fine). Nothing worse than rank 20 is ever picked; if fewer than 10 stocks qualify at all
(score > 0), the rest stays cash (rare). Weights (∝ 1/volatility) and the soft QQQ filter are unchanged.

**Mid-week swap rule:** if a stock you do NOT hold ranks in the **top 3** and a stock you hold has fallen **below rank 15** (or no
longer qualifies, score ≤ 0), sell the worst-ranked holding and buy the new stock **with the same dollar amount** at the next open.
With T20 the top-3 stock always qualifies, whatever its sector; several swaps can happen at one check.

**Mid-week exit (rank-30 safety net, MW30, live from 2026-09-25, user decision):** after the swap step, any holding ranked
**worse than 30** (or no longer ranked) is sold at the next open and the **cash stays idle until the Friday rebalance**.
Nothing else is traded mid-week.

The ranking uses price bars only (Technical_Score from OHLCV + relative strength from closes); news sentiment and fundamentals are
shown for context but are not part of the score. At the end the run prints e.g.
`Mid-week check at the Mon Sep 28 close: SELL X (rank 22) and BUY Y (rank 2) at the Tue Sep 29 open, same dollar amount.` or
`No swap (...)`, plus the next decision. The same lines are in `Reports/strategy_midweek_check.csv` and in the app's **This week** box
(Dashboard tab). On other days (Tue/Thu or a rerun) it shows this week's latest decision and the session it applies to.
`paper_trade.py` (auto) turns a Mon/Wed swap into SELL-all-of-X / BUY-Y-for-the-same-dollars and a rank-30 exit into a SELL-only
order (the cash waits for Friday); `--target midweek` forces that. Exit lines read e.g.
`Mid-week check at the Mon Sep 21 close: SELL APA (rank 34, worse than 30) at the Tue Sep 22 open, hold the cash until the Friday rebalance.`

**Holdings alert (top of the app, and the ALERT line of `python run_all.py`):** one plain line per action, tickers link to
the app (`http://localhost:8501/?symbol=XXX`), e.g. `Swap at the Tue Sep 29 open: sell X and buy Y (rank 2), same dollar amount.`
(Mon/Wed check day), `Sell TRGP (rank 45) at the Tue Sep 29 open, hold cash until Friday.` (Mon/Wed check day, rank-30 exit; shown
next to any swap line), `With today's ranks the swap rule would sell ANET and buy RBRK (rank 1).` / `With today's ranks the rank-30
exit would sell ...` (other days, for reference),
`No swap with today's ranks. Next check: Mon Sep 28.`, or `Full rebalance at the Mon Sep 28 open: sell ...; buy ...` (Friday).
It uses the strategy's holdings unless **`my_positions.csv`** exists in the project folder — then it checks YOUR holdings with
the same rules (top 3 in, below rank 15 out, then sell anything worse than rank 30; stocks outside the 96 count as not ranked, so
the exit flags them). If this week's Mon/Wed check called for a swap or an exit that your file shows as not done, the alert says so
(`The Wed Sep 23 check called for selling ...`). Format = the one
`paper_trade.py --positions` reads: `Symbol,Shares` (Shares optional; an optional `Weight` column in % also works for the alert).
Copy `my_positions.example.csv` (placeholder share counts) to `my_positions.csv` and edit it; the file is in `.gitignore`.
The app shows a switch (your positions file / strategy holdings) under Details → Data freshness and settings when the file
exists. `python run_all.py --sync-paper` or `alpaca_paper_account.ipynb` writes the file from your Alpaca paper account. Also: `python holdings_alert.py
[--positions f.csv | --strategy]` and `python run_all.py --positions f.csv`.

**Revert** (in `backtest_engine.py`, then `python run_all.py`; each step is independent):
- picks from any rank with the hard max-4 limit (C6-U96-MW30): `WINNER["max_pick_rank"] = None` and `WINNER["cap_soft"] = False`
- no rank-30 exit (plain mid-week swap): `WINNER["midweek_exit_below"] = None`
- weekly-only (C6-U96): `WINNER["midweek_swap"] = None`

Backtests (2022-04 → 2026-09-24, 0.1%/side, never-seen = 2022-04 → 2024-09; last 1y / 2y vs QQQ +24.9% / +54.2%):

| Rules | Total | Sharpe | Max DD | Never-seen Sharpe | Last 1y | Last 2y |
|-------|-------|--------|--------|-------------------|---------|---------|
| C6-U96 weekly only | +390.6% | 1.40 | −32.6% | 0.66 | | |
| C6-U96-MW (`Reports/rebalance_frequency_test.csv`, variant D) | +438.08% | 1.4649 | −31.84% | 0.8414 | +41.8% | +232.7% |
| C6-U96-MW30 (`Reports/sell_rule_test.csv`, S3) | +421.59% | 1.4695 | −32.18% | 0.8172 | +45.3% | +228.9% |
| **C6-U96-T20-MW30 (live)** — user decision, not a pre-tested rule | +414.07% | 1.3716 | −32.19% | 0.5958 | +54.4% | +267.5% |

The live rules have the weakest never-seen result of the four (0.60): they lean on the recent Tech run (up to 6 Tech names). Mid-week trades
per year: MW ≈16 swaps; MW30 ≈16 swaps + ≈29 exits; T20-MW30 ≈32 swaps (top-3 swaps ignore the sector limit) + ≈18 exits. `tests/test_midweek_repro.py` confirms the live
engine reproduces all three mid-week variants exactly (the first two against the original tests, T20 against an independent
re-implementation) and matches the pipeline's decision history; `tests/test_rank_audit.py` re-derives the ranks, picks (incl. T20
relaxed-cap weeks) and recent mid-week checks (incl. ≥ 2 rank-30 sells) from raw bars.

## Project layout

- `run_all.py` / `run_all.ipynb` — the one command; `app.py` — Streamlit dashboard; `backtest_engine.py` — live rules (`WINNER`) and
  the backtest engine; `sector_mapping.py` — universe and sectors; `holdings_alert.py`, `paper_trade.py` — alert and dry-run orders.
- Notebooks: the pipeline steps below plus `strategy_backtest_v2/v3/v4.ipynb` (research, opt-in with `--backtests`).
- `alpaca_paper.py` + `alpaca_paper_account.ipynb` — read-only view of the Alpaca PAPER account (see Paper account below).
- `tests/` — `run_tests.py` runs `test_midweek_repro.py`, `test_rank_audit.py`, `test_runner.py`, `test_app.py`,
  `test_paper_account.py` (mock server), `test_dashboard_http.py` (port 8599) (shared read-only setup in `backtest_setup.py`;
  nothing is written to `Reports/`).
- `Reports/` — every output (below) and `logs/`; `docs/` — this file and the per-notebook docs.

## Run order and outputs (all in `Reports/`)

| # | Step | Output | Read by | Quota APIs |
|---|------|--------|---------|------------|
| 1 | `company_report_autofetch.py` (full mode) | `balance_sheet.csv`, `fetch_run_log.csv` | processing | Alpha Vantage |
| 2 | `company_report_processing.ipynb` | `complete_company_analysis.xlsx` | scoring, visualization, app | – |
| 3 | `company_report_scoring.ipynb` | `balance_sheet_weights.csv` | main | – |
| 4 | `sentiment_analysis.ipynb` | `news_cleaned_df.csv`, `weighted_sentiment.csv`, `sentiment_history.csv` | main, app | NewsAPI, Finnhub (online mode) |
| 5 | `earnings_date.ipynb` | `earnings_date.csv` | main, app, backtests | Finnhub (online mode) |
| 6 | `main_signal_analysis.ipynb` | `signal_analysis.csv`, `strategy_picks.csv`, `strategy_changes.csv`, `strategy_holdings.csv`, `strategy_tracking.csv`, `strategy_decisions.csv`, `strategy_midweek_check.csv`, `benchmark_prices.csv`, `factor_history.csv` | app, `paper_trade.py` | – |
| 7 | `company_report_visualization.ipynb` (opt-in) | inline charts (`COMPANY_SYMBOL=NVDA` env picks the stock) | you | – |
| 8 | `strategy_backtest_v3.ipynb` (opt-in) | `strategy_comparison_v3.csv`, `strategy_walkforward_v3.png` | – | – |
| 9 | `strategy_backtest_v4.ipynb` (opt-in) | `strategy_comparison_v4.csv`, `trade_diagnostics_v4.csv` (+ `_summary`, `_by_stock`), `strategy_walkforward_v4.png`, `trade_diagnostics_v4.png` | app | – |
|   | `strategy_backtest_v2.ipynb` (opt-in, historical) | `strategy_comparison_v2.csv`, `strategy_equity_curves_v2.png`, … | – | – |

`app.py` (Streamlit) is the deployed file; git tracks it together with the reports it reads. The app maintains `daily_rank.csv`.

### Notebook modes (environment variables, set automatically by `run_all.py`)

| Variable | Values | Meaning |
|----------|--------|---------|
| `PIPELINE_SENTIMENT_MODE` | `online` (default by hand) / `offline` / `sample` | offline = re-apply relevance filter + scores to cached news, no network; sample = 1–2 symbols (`PIPELINE_SAMPLE_SYMBOLS`) written only to `Reports/cache/sample_*` |
| `PIPELINE_EARNINGS_MODE` | `online` / `offline` / `sample` | offline = normalize/dedupe the existing file; online merges new dates into the file (past dates are kept) |
| `COMPANY_SYMBOL` | ticker | stock shown by the visualization notebook |

## Live strategy (rules C6 on the 96-stock universe, picks from ranks 1–20, mid-week swap + rank-30 exit = tag **C6-U96-T20-MW30**, `backtest_engine.WINNER`)

**Live from 2026-09-25 (two user decisions): C6-U96-T20-MW30** = C6-U96-MW below + `WINNER["midweek_exit_below"] = 30` (MW30:
sell-rule test variant S3; it missed the pre-declared switch rule only on the never-seen period, 0.82 vs 0.84) + `WINNER["max_pick_rank"]
= 20`, `WINNER["cap_soft"] = True` (T20: "based on my gut", no pre-test; backtest shown above for information). Rules in *How to use*.
Tracking rows up to 2026-09-24 keep their labels (`C6`); rows from the next session are `C6-U96-T20-MW30`, chain-linked (no jump). As with
every rule change, the decision history is recomputed with the new rules from the start of the window, so it shows e.g. APA sold at the
Mon Sep 21 check (rank 34) — you actually still hold what you bought; the Friday 9/25 rebalance re-aligns everything.
`signal_analysis.csv` `rules_version` = `v4-mw30-t20`; `strategy_midweek_check.csv` has `Action` SELL rows for exits.

**Live since 2026-09-24 (user decision): C6-U96-MW** = C6-U96 below + `WINNER["midweek_swap"] = {"enter_top": 3, "exit_below": 15,
"days": ["Mon", "Wed"]}` (see *How to use*). Chosen from the rebalance-frequency test (`Reports/rebalance_frequency_test.csv`):
weekly-only +391% / Sharpe 1.40 / never-seen 0.66; Mon/Wed/Fri full rebalance (+184%) and daily rebalance (+169%) did worse after
costs; "top 3 in, below 15 out" = +438% / 1.46 / never-seen 0.84 (picked by the user). Trading: ≈217 trades/yr vs ≈213 weekly-only. Tracking rows up to
2026-09-24 keep their labels (`C6`); MW was superseded by C6-U96-T20-MW30 before any `C6-U96-MW` tracking row was written.
`strategy_decisions.csv` now also holds the mid-week swap days (column `Check` = weekly / mid-week); `signal_analysis.csv` has
`Midweek_Check` = 1 on check sessions and `rules_version` `v4-mw`.

**Live since 2026-09-24 (user decision, later the same day): C6-U96** = C6-U91 + 5 emerging-tech names, all Technology (XLK):
CRDO (Credo), NBIS (Nebius), LITE (Lumentum), CLS (Celestica), RBRK (Rubrik) → 96 tradable + QQQ
(`sector_mapping.EXPANDED_UNIVERSE = "u96"`). Same C6 rules (ranks 1–10, max 4 per sector, soft QQQ regime, RS vs sector ETF).
**Hindsight caveat:** these 5 were picked on 2026-09-24 news after big run-ups, so any backtest including them is flattered.
Even so, the backtest did not improve: total +391%, Sharpe 1.40, max DD −32.6%, never-seen Sharpe 0.66, last 1y +47.0%, last 2y +237.6%
(U91: +410%, 1.45, −31.3%, 0.72, +46.4%, +242.9%; 78: +452%, 1.47, −28.6%, 0.92, +27.2%, +220.9%) — `Reports/universe_u96_comparison.csv`.
Short histories: the 200-bar eligibility rule applies (RBRK ranks from 2025-02-11, NBIS from 2025-08-08); NBIS bars before 2024-10-21
(Yandex N.V. history incl. a flat zero-volume 2022–24 halt) are dropped via `sector_mapping.HISTORY_START`.
Tracking rows up to 2026-09-24 keep the label `C6`; later rows are `C6-U96`, chain-linked (no jump).
**Revert:** `EXPANDED_UNIVERSE = "high_beta_91"` (U91) or `None` (the 78), then `python run_all.py`.
The online news step makes ≈97 NewsAPI calls (96 stocks + QQQ; free limit 100/day — retries on transient errors count too) + ≈97 Finnhub.

Previous step (same day):

**Live since 2026-09-24 (user decision): C6-U91** = the C6 rules below, unchanged (ranks 1–10, max 4 per sector, soft QQQ regime,
relative strength vs the sector ETF — not the sector median), on the original 78 stocks **+ 13 names with 2019–21 beta ≥ 1.5**:
APA OXY TRGP DVN FANG (Energy), COF C (Financials), BE BA URI PH (Industrials), FCX LYB (Materials) → 91 tradable + QQQ
(`sector_mapping.EXPANDED_UNIVERSE = "high_beta_91"`). Backtest (stitched walk-forward 2022-04 → 2026-09-24): total +410%, Sharpe 1.45,
max DD −31.3%, last 1y +46.4%, last 2y +242.9% (78: +452%, 1.47, −28.6%, +27.2%, +220.9%). **It failed the pre-declared never-seen test**
(2022-04 → 2024-09 Sharpe 0.72 vs 0.92 for the 78; bootstrap P(Sharpe > 78) = 0.51, i.e. a tie) and was adopted by user choice for its
recent strength. Tracking rows before the switch (2026-09-21 → 09-24) keep the label `C6`; U96 replaced U91 before any `C6-U91` row was written.
(Revert to U91: `EXPANDED_UNIVERSE = "high_beta_91"`; to the 78: `None`; then `python run_all.py`.)
New names have no cached news/fundamentals/earnings until the next online runs (sentiment 0, fundamentals "—", except COF/FANG/FCX
fundamentals); (With U96 the online news fetch is ≈97 calls, see above.)

**C6** – weekly (last NYSE session of the week, decided at the close, filled at the next open): top 10 stocks by
Strategy Score = 0.5 × Technical_Score + 0.5 × relative strength (vs sector ETF and SPY), score > 0, **max 4 per sector**
(walk down the ranks; a stock whose sector already has 4 picks is skipped),
weights ∝ 1 / 63-day volatility. **Soft regime**: when QQQ closes at/below its 200-day SMA on the rebalance day, all weights are
halved (rest in cash). Chosen with a rolling quarterly walk-forward (train 12 months, trade 3 months) in
`strategy_backtest_v3.ipynb` (soft regime). `strategy_backtest_v4.ipynb` (2026-09-24) also tested rank buffers (hold until rank
> 15/20/25), score-only exits, a 4-week minimum hold, 2-weekly/monthly rebalancing, MA50/ATR stops and an absolute score
threshold — none beat the weekly rank rule. Sector caps were tested the same day and **not adopted (user decision)**: cap 2 (C6b)
met the pre-declared rule by a hair and was live briefly, but cap 2 vs 4 is statistically tied and cap 2 lagged badly over the
last 12 months; caps 5–8 and no cap had higher stitched Sharpe but failed the never-seen 2022–24 test
(`Reports/strategy_comparison_sector_caps.csv`, `Reports/strategy_vs_qqq_1y_2y.csv`). See those notebooks for the full
comparison and the selection-bias caveats.

**Universe expansion (tested 2026-09-24; `u96` live, see above):** a fixed-rule expansion to at least 10 stocks per sector (largest SPDR
sector-ETF holdings) is prepared behind the one-step switch `sector_mapping.EXPANDED_UNIVERSE` (`None` = original 78 stocks; `"high_beta_91"` = U91; live = `"u96"`).
Options: `"u96"` (live), `"high_beta_91"`, `"high_beta_84"`, `"fast_sectors_98"` (+5 high-beta names in Energy/Financials/Industrials/Materials), `"existing_sectors"` (102), `"all_sectors"` (142).
None beat the current universe in the backtest (see [universe_expansion.md](universe_expansion.md)).

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
