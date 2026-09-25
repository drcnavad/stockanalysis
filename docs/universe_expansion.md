# Universe expansion test (2026-09-24) — `u96` (C6-U96) LIVE by user decision

> **Update 2026-09-24 (user decision):** the live rules are now **C6-U96-MW** = this `u96` universe + the Mon/Wed mid-week swap
> (`backtest_engine.WINNER["midweek_swap"]`, top 3 in / below rank 15 out; backtest +438%, Sharpe 1.46). See docs/README.md,
> *How to use*. Revert the swap only: `WINNER["midweek_swap"] = None`; the universe switch below is independent.

> **Live since 2026-09-24 (user decision, later the same day): C6-U96** = C6-U91 + 5 emerging-tech names, all Technology (XLK):
> CRDO (Credo), NBIS (Nebius), LITE (Lumentum), CLS (Celestica), RBRK (Rubrik) → 96 tradable + QQQ
> (`sector_mapping.EXPANDED_UNIVERSE = "u96"`). Same C6 rules (ranks 1–10, max 4 per sector, soft QQQ regime, RS vs sector ETF).
> **Hindsight caveat:** these 5 were picked on 2026-09-24 news after big run-ups, so any backtest including them is flattered.
> Even so, the backtest did not improve: total +391%, Sharpe 1.40, max DD −32.6%, never-seen Sharpe 0.66, last 1y +47.0%, last 2y +237.6%
> (U91: +410%, 1.45, −31.3%, 0.72, +46.4%, +242.9%; 78: +452%, 1.47, −28.6%, 0.92, +27.2%, +220.9%) — `Reports/universe_u96_comparison.csv`.
> Short histories: the 200-bar eligibility rule applies (RBRK ranks from 2025-02-11, NBIS from 2025-08-08); NBIS bars before 2024-10-21
> (Yandex N.V. history incl. a flat zero-volume 2022–24 halt) are dropped via `sector_mapping.HISTORY_START`.
> Tracking rows up to 2026-09-24 keep the label `C6`; later rows are `C6-U96`, chain-linked (no jump).
> **Revert:** `EXPANDED_UNIVERSE = "high_beta_91"` (U91) or `None` (the 78), then `python run_all.py`.
> `--online-news` now makes ≈97 NewsAPI calls (96 stocks + QQQ; free limit 100/day — retries on transient errors count too) + ≈97 Finnhub.
>
> **Earlier the same day — C6-U91:**

> **LIVE since 2026-09-24 (user decision): `EXPANDED_UNIVERSE = "high_beta_91"` → tag C6-U91** = the 78 + 13 U98 additions with
> 2019–21 beta ≥ 1.5 (APA OXY TRGP DVN FANG COF C BE BA URI PH FCX LYB), C6 rules unchanged (ranks 1–10, max 4 per sector, soft QQQ
> regime, RS vs sector ETF). Backtest: +410% total, Sharpe 1.45, max DD −31.3%, last 1y +46.4%, last 2y +242.9%. It **failed** the
> pre-declared never-seen test (Sharpe 0.72 vs 0.92 for the 78; bootstrap P = 0.51 = tie) and was adopted by user choice for its recent
> strength. Tracking rows up to 2026-09-24 stay labelled `C6`; U96 replaced U91 before any `C6-U91` row was written. Decision history in
> `strategy_decisions.csv` is backfilled under the live rules (now U96). **Revert:** `EXPANDED_UNIVERSE = "high_beta_91"` (U91) or `None` (78) in
> `sector_mapping.py`, then `python run_all.py`. The sections below are the original test write-ups (their "NOT LIVE" labels describe the state at the time).

**Question (user):** add at least 10 stocks for every sector so the sector cap is not a choice between 2–3 names.

## Selection rule (fixed, not hand-picked)
For every sector with fewer than 10 tradable stocks, add the largest holdings of that sector's SPDR ETF **by ETF weight**, skipping
names already in the list and second share classes (GOOG, FOX, NWS skipped; GOOGL kept). Source: SSGA daily holdings files
(`https://www.ssga.com/us/en/intermediary/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-<etf>.xlsx`),
holdings **as of 23-Sep-2026**, downloaded 2026-09-24. Every added name has Alpaca daily bars (market-data endpoint) and a 3-month
average dollar volume of at least $248M/day. Late listings: **CEG** (from 2022-02) and **WBD** (from 2022-04) — the engine handles them
like CRCL/FIG (a stock becomes eligible after 200 bars). **VMRK** is Equity Residential renamed after its AvalonBay merger (ticker since
2026-08-18; the earlier history is EQR's). Technology already has 42 names, so nothing was added there. Per-name list:
`Reports/universe_expansion_added.csv`.

| sector | ETF | before | added | after (all 11) | added names |
|---|---|---|---|---|---|
| Communication Services | XLC | 7 | 3 | 10 | WBD T DIS |
| Consumer Discretionary | XLY | 6 | 4 | 10 | HD MCD TJX BKNG |
| Consumer Staples | XLP | 0 | 10 | 10 | WMT COST PG KO PM MO TGT MDLZ CL PEP |
| Energy | XLE | 4 | 6 | 10 | XOM CVX COP PSX MPC VLO |
| Financials | XLF | 5 | 5 | 10 | BRK.B JPM V MA BAC |
| Health Care | XLV | 7 | 3 | 10 | LLY JNJ ABBV |
| Industrials | XLI | 7 | 3 | 10 | CAT GE RTX |
| Materials | XLB | 0 | 10 | 10 | LIN NEM FCX SHW ECL VMC STLD APD MLM NUE |
| Real Estate | XLRE | 0 | 10 | 10 | WELL PLD EQIX AMT SPG PSA VTR CBRE DLR VMRK |
| Technology | XLK | 42 | 0 | 42 |  |
| Utilities | XLU | 0 | 10 | 10 | NEE SO DUK CEG AEP D SRE ETR XEL VST |

Two variants were tested: **existing 7 sectors → 10** (+24 = 102 stocks) and **all 11 sectors → 10** (+64 = 142, adds Consumer
Staples, Materials, Real Estate and Utilities, which the current list does not cover at all).

**Survivorship bias remains:** these are *today's* largest companies, so the expanded universe is still chosen with hindsight (less
than the hand-picked 78: an equal-weight buy & hold of the current 78 made +199% from 2022-04 vs +148% for the 142).

## Backtest (C6 rules unchanged: weekly top 10, cap 4, soft QQQ regime, inverse-vol weights, 0.1%/side, next-open fills)
Stitched 2022-04-01 → 2026-09-24; never-seen 2022-04-01 → 2024-09-16. Last 1y/2y = close-to-close from 2025-09-24 / 2024-09-24.
Relative strength is a percentile rank within the universe, so adding names also changes the scores of existing names.

| | CAGR % | Total % | Sharpe | Max DD % | Turnover x/yr | Win % | Never-seen Sharpe | Never-seen DD % | Last 1y % | Last 2y % | 2022 (Apr–) | 2023 | 2024 | 2025 | 2026 YTD | Cap binds % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Current 78 | 46.7 | 452 | 1.47 | -28.6 | 39.8 | 46.1 | 0.92 | -22.3 | 27.2 | 220.9 | -4.6 | 32.3 | 142.0 | 63.0 | 10.8 | 57 |
| Expanded 7 sectors (102) | 35.7 | 290 | 1.31 | -24.3 | 45.5 | 45.8 | 0.63 | -24.3 | 35.4 | 186.9 | -8.2 | 17.4 | 97.7 | 49.0 | 22.9 | 57 |
| Expanded all 11 sectors (142) | 31.2 | 236 | 1.20 | -22.8 | 48.9 | 45.9 | 0.92 | -22.8 | 15.7 | 121.7 | -10.5 | 20.9 | 107.1 | 34.4 | 11.6 | 45 |
| QQQ | 18.1 | 110 | 0.85 | -29.1 | 0.2 |  | 0.61 | -29.1 | 24.9 | 54.2 | -26.1 | 54.9 | 25.6 | 20.8 | 21.1 |  |
| SPY | 14.0 | 80 | 0.85 | -21.3 | 0.2 |  | 0.67 | -21.3 | 17.3 | 37.4 | -14.6 | 26.2 | 24.9 | 17.7 | 13.4 |  |
| EW B&H current 78 (hindsight check) | 27.8 | 199 | 0.86 | -35.8 | 0.2 |  | 0.69 | -35.8 | 7.8 | 87.3 | -31.6 | 76.9 | 54.2 | 38.5 | 15.6 |  |
| EW B&H expanded 142 | 22.6 | 148 | 0.95 | -28.0 | 0.2 |  | 0.82 | -25.7 | 9.7 | 62.2 | -17.4 | 41.9 | 41.0 | 31.6 | 14.1 |  |

Paired block bootstrap (cap 4, expanded 142 vs current 78): P(Sharpe higher) = 0.14,
90% range of the Sharpe difference -0.60 to +0.11. Pre-declared rule (Sharpe higher AND max DD within
2 pts AND never-seen Sharpe ≥): **not met** by either variant.

Sector-cap check on each universe (stitched Sharpe / never-seen Sharpe; report only, live stays cap 4):

| universe | cap 2 | cap 3 | cap 4 | cap 5 | no cap |
|---|---|---|---|---|---|
| Current (78) | 1.48 / 1.06 | 1.33 / 0.66 | 1.47 / 0.92 | 1.43 / 0.78 | 1.64 / 0.82 |
| Expanded 7 sectors (+24 = 102) | 1.38 / 0.95 | 1.21 / 0.61 | 1.31 / 0.63 | 1.35 / 0.63 | 1.62 / 0.82 |
| Expanded all 11 sectors (+64 = 142) | 1.13 / 0.77 | 1.17 / 0.79 | 1.20 / 0.92 | 1.29 / 0.98 | 1.52 / 1.10 |

Holdings under the 142-stock universe (cap 4): official 2026-09-18 = ABBV ANET FTNT GTLB HOOD MPC PSX TEAM TEM VLO; preview 2026-09-24 =
AMD BIIB HOOD META PANW TEAM TEM TWLO VLO WBD.

## Pipeline / app impact (tested in a sandbox copy, live outputs untouched)
- the old `run_pipeline.py` (offline): 24.0 s → 26.3 s (main step 7 s → 11 s); Alpaca bars requested in 4 chunks instead of 3.
- Offline sentiment / earnings make no API calls; new names get SentimentScore 0 (neutral) and no earnings dates until an online run.
  **Online runs would cost more quota**: `--online-news` ≈ 143 + 143 NewsAPI/Finnhub calls instead of 79 + 79 (NewsAPI's free tier
  is 100 requests/day), `--online-earnings` ≈ 143 Finnhub calls. `--fetch-fundamentals` stays at 12 stocks/day, but the rotation grows
  from 128 to 180 symbols. Missing fundamentals show as "—" (display only; the strategy does not use them).
- App: dropdown lists all 143 symbols, charts render for new names (BRK.B, CEG, WBD, VLO, KO, VMRK checked), Signals now / Rank /
  slots consistent; rank caption counts ranked stocks dynamically.

## One-step go-live (only after review)
In `sector_mapping.py` set `EXPANDED_UNIVERSE = "all_sectors"` (or `"existing_sectors"`), then run `python run_all.py`.
The strategy tag becomes `C6-U142` (or `C6-U102`) automatically, so new tracking rows are distinguishable; existing rows keep `C6`.
The next weekly decision would rebalance into the expanded picks. Undo: set it back to `None` and rerun.

Results: `Reports/universe_expansion_comparison.csv`, `Reports/universe_expansion_added.csv` (the research scripts and charts were moved to the Trash in the 2026-09-25 cleanup; the result CSVs are kept).


---

# Revision (user, 2026-09-24): U98 — +5 names in the 4 fastest sectors, keep the 78 — NOT LIVE

**Request:** add 5 per sector instead of 10, leave slow sectors (e.g. Communication Services) out, keep all 78 current stocks, stay under 100.
Existing Communication Services names (META, GOOGL, …) stay; nothing is added to Communication Services, Consumer Staples, Utilities,
Real Estate or Technology.

**Rule (fixed):** rank XLE/XLF/XLI/XLY/XLV/XLB by beta to SPY over 2019-01-01 → 2021-12-31 (before the backtest window, no hindsight on
returns): XLE 1.32, XLF 1.19, XLI 1.10, XLB 1.08, XLY 1.00, XLV 0.82 → top 4 = Energy, Financials, Industrials, Materials. Per sector:
the top 30 holdings (SSGA files as of 23-Sep-2026) not already listed, no second share classes, full Alpaca history over 2019-2021 and no
>100% one-day move (a spliced history — this excluded EXE, ex-Chesapeake across its 2020-21 bankruptcy), then the 5 highest 2019-2021
betas (tiebreak ETF weight). Late listings without 2019 data (GEV, HWM, CTVA, AMCR) are excluded. Notes: SW's history before 2024-07 is
WestRock's; APO merged with Athene in 2022. All 20 trade ≥ $187M/day. Survivorship bias remains (today's holdings).
Details: `Reports/universe_expansion_u98_added.csv` (all 163 candidates with betas and status).

| sector (ETF beta 2019-21) | added (beta 2019-21, ETF weight %) |
|---|---|
| Materials (1.08) | FCX (1.71, 5.66), LYB (1.51, 2.32), STLD (1.38, 4.45), MOS (1.34, 1.16), SW (1.32, 3.66) |
| Energy (1.32) | APA (2.04, 0.81), OXY (2.00, 2.20), TRGP (1.89, 3.19), DVN (1.79, 2.78), FANG (1.68, 2.00) |
| Financials (1.19) | COF (1.57, 1.56), C (1.56, 2.88), APO (1.46, 0.76), MS (1.45, 3.08), AXP (1.44, 2.07) |
| Industrials (1.10) | BE (2.00, 1.50), BA (1.75, 2.92), URI (1.65, 1.19), PH (1.53, 2.26), TDG (1.44, 1.13) |

Per-sector counts after: Energy 4→9, Financials 5→10, Industrials 7→12, Materials 0→5; others unchanged (Technology 42,
Communication Services 7, Health Care 7, Consumer Discretionary 6) → 98 tradable (+ QQQ).

## Backtest (same C6 rules, cap 4; stitched 2022-04-01 → 2026-09-24; never-seen 2022-04-01 → 2024-09-16)

| | CAGR % | Total % | Sharpe | Max DD % | Turnover x/yr | Win % | Never-seen Sharpe | Never-seen DD % | Last 1y % | Last 2y % | 2022 (Apr–) | 2023 | 2024 | 2025 | 2026 YTD | Cap binds % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Current 78 | 46.7 | 452 | 1.47 | -28.6 | 39.8 | 46.1 | 0.92 | -22.3 | 27.2 | 220.9 | -4.6 | 32.3 | 142.0 | 63.0 | 10.8 | 57 |
| U98 | 37.6 | 315 | 1.32 | -30.7 | 44.9 | 47.1 | 0.68 | -24.4 | 38.6 | 189.4 | -9.5 | 19.0 | 110.2 | 52.1 | 20.6 | 57 |
| U142 | 31.2 | 236 | 1.20 | -22.8 | 48.9 | 45.9 | 0.92 | -22.8 | 15.7 | 121.7 | -10.5 | 20.9 | 107.1 | 34.4 | 11.6 | 45 |
| QQQ | 18.1 | 110 | 0.85 | -29.1 | 0.2 |  | 0.61 | -29.1 | 24.9 | 54.2 | -26.1 | 54.9 | 25.6 | 20.8 | 21.1 |  |
| SPY | 14.0 | 80 | 0.85 | -21.3 | 0.2 |  | 0.67 | -21.3 | 17.3 | 37.4 | -14.6 | 26.2 | 24.9 | 17.7 | 13.4 |  |
| EW B&H 78 | 27.8 | 199 | 0.86 | -35.8 | 0.2 |  | 0.69 | -35.8 | 7.8 | 87.3 | -31.6 | 76.9 | 54.2 | 38.5 | 15.6 |  |
| EW B&H U98 | 26.4 | 185 | 0.88 | -33.8 | 0.2 |  | 0.68 | -31.6 | 13.3 | 85.5 | -25.8 | 60.8 | 47.4 | 36.3 | 18.7 |  |
| EW B&H U142 | 22.6 | 148 | 0.95 | -28.0 | 0.2 |  | 0.82 | -25.7 | 9.7 | 62.2 | -17.4 | 41.9 | 41.0 | 31.6 | 14.1 |  |

Paired block bootstrap (cap 4, U98 vs current 78): P(Sharpe higher) = 0.22, 90% range of the
difference -0.38 to +0.16. Pre-declared rule: **not met**.

Sector cap check (stitched / never-seen Sharpe; report only):

| universe | cap 2 | cap 3 | cap 4 | cap 5 | no cap |
|---|---|---|---|---|---|
| Current (78) | 1.48 / 1.06 | 1.33 / 0.66 | 1.47 / 0.92 | 1.43 / 0.78 | 1.64 / 0.82 |
| U98 fast sectors (+20 = 98) | 1.64 / 0.86 | 1.42 / 0.62 | 1.32 / 0.68 | 1.34 / 0.62 | 1.52 / 0.70 |

Holdings under U98 (cap 4): official 2026-09-18 = ANET APA FTNT GTLB HOOD META MRK TEAM TEM TRGP; preview 2026-09-24 = AMD BIIB DVN HOOD META MRK PANW TEAM TEM TWLO.

**Pipeline/app (sandbox copy):** offline pipeline 24.3 s (unchanged), 99 symbols in signal_analysis, app dropdown 99, charts/Signals
now/rank/slots OK, tag `C6-U98`. Online news would need 99 NewsAPI calls per run (free tier 100/day; each symbol can retry up to 3×, so
one retry-heavy run can exceed it) + 99 Finnhub; online earnings 99 Finnhub calls; fundamentals rotation 128 → 146 symbols (still 12/day).

**Go-live (only after review):** `EXPANDED_UNIVERSE = "fast_sectors_98"` in `sector_mapping.py`, then `python run_all.py`.
Results: `Reports/universe_expansion_u98_comparison.csv`, `Reports/universe_expansion_u98_added.csv` (the research scripts and charts were moved to the Trash in the 2026-09-25 cleanup; the result CSVs are kept).


---

# Follow-up (user, 2026-09-24): high-beta subsets + median-based relative strength — U91 / RS etf LATER MADE LIVE (see top)

- **U91** (`EXPANDED_UNIVERSE = "high_beta_91"`): U98 additions with 2019-21 beta ≥ 1.5 → APA OXY BE TRGP DVN BA FCX FANG URI COF C PH LYB.
- **U84** (`"high_beta_84"`): beta ≥ 1.75 → APA OXY BE TRGP DVN BA.
- **RS benchmark** (`backtest_engine.WINNER["rs_benchmark"]`, live `"etf"`): `"sector_median"` = stock vs the median return of its sector
  peers in the universe (leave-one-out, ≥ 3 peers with data, else the sector ETF), sector-vs-SPY half unchanged; `"median_all"` = also
  sector median vs universe median. Same 21/63/126-day windows, cross-sectional per date (no lookahead). Verified by the independent
  audit (then `Reports/logs/rank_audit.py`, now `tests/test_rank_audit.py`, re-implements the median benchmark too).

| combo (cap 4 unless noted) | CAGR % | Total % | Sharpe | Max DD % | Never-seen Sharpe | Last 1y % | Last 2y % | P(Sharpe > live) | Passes |
|---|---|---|---|---|---|---|---|---|---|
| QQQ buy & hold | 18.1 | 110 | 0.85 | -29.1 | 0.61 | 24.9 | 54.2 |  |  |
| SPY buy & hold | 14.0 | 80 | 0.85 | -21.3 | 0.67 | 17.3 | 37.4 |  |  |
| C6 cap 4 | 78 | RS etf | 46.7 | 452 | 1.47 | -28.6 | 0.92 | 27.2 | 220.9 |  |  |
| C6 cap 4 | 78 | RS sector_median | 47.9 | 472 | 1.50 | -27.1 | 1.05 | 17.9 | 214.6 | 0.65 | True |
| C6 cap 4 | 78 | RS median_all | 43.7 | 403 | 1.38 | -30.1 | 0.93 | 17.8 | 188.4 | 0.24 | False |
| C6 cap 4 | U84 | RS etf | 45.9 | 438 | 1.46 | -30.6 | 0.75 | 48.1 | 253.7 | 0.53 | False |
| C6 cap 4 | U84 | RS sector_median | 46.7 | 452 | 1.48 | -28.2 | 0.81 | 35.9 | 250.6 | 0.57 | False |
| C6 cap 4 | U84 | RS median_all | 38.8 | 332 | 1.26 | -32.9 | 0.78 | 16.2 | 174.9 | 0.10 | False |
| C6 cap 4 | U91 | RS etf | 44.1 | 410 | 1.45 | -31.3 | 0.72 | 46.4 | 242.9 | 0.51 | False |
| C6 cap 4 | U91 | RS sector_median | 37.9 | 320 | 1.28 | -29.7 | 0.45 | 37.7 | 234.8 | 0.13 | False |
| C6 cap 4 | U91 | RS median_all | 40.1 | 350 | 1.31 | -28.9 | 0.77 | 25.1 | 192.6 | 0.19 | False |
| C6 cap 4 | U98 | RS etf | 37.6 | 315 | 1.32 | -30.7 | 0.68 | 38.6 | 189.4 | 0.22 | False |
| C6 cap 4 | U98 | RS sector_median | 40.5 | 356 | 1.37 | -28.7 | 0.60 | 40.8 | 237.1 | 0.31 | False |
| C6 cap 4 | U98 | RS median_all | 37.3 | 311 | 1.27 | -28.4 | 0.57 | 35.6 | 199.3 | 0.15 | False |
| C6 cap 2 | 78 | RS sector_median | 38.2 | 323 | 1.37 | -24.2 | 1.05 | -2.4 | 136.2 | 0.40 | False |
| C6 cap none | 78 | RS sector_median | 59.1 | 694 | 1.65 | -27.1 | 0.91 | 101.7 | 372.2 | 0.76 | False |
| C6 cap 2 | U84 | RS sector_median | 46.1 | 443 | 1.60 | -25.5 | 1.08 | 20.8 | 205.2 | 0.78 | True |
| C6 cap none | U84 | RS sector_median | 56.7 | 642 | 1.61 | -28.2 | 0.73 | 112.5 | 389.4 | 0.73 | False |

Pre-declared rule vs live (Sharpe higher, max DD within 2 pts, never-seen Sharpe ≥ 0.92): **78 / sector_median / cap 4 passes**
(Sharpe 1.50 vs 1.47, DD −27.1% vs −28.6%, never-seen 1.05 vs 0.92) but is statistically tied (bootstrap P = 0.65) and lagged over the
last 12 months (+17.9% vs +27.2%). U84 / sector_median / cap 2 also passes but it came from a post-hoc cap check (report only).

**Median vs mean:** equal-weight stock returns are heavily right-skewed. Since 2022-04 the mean stock in the 78 returned +201.5% but the
median +80.5%; over the last year mean +20.1% vs median −1.8%. Technology over the last year: mean +39.1%, median +0.9% (MU +569%,
AMD +291%, MRVL +224%). Every strategy's median closed trade is negative (≈ −0.4% to −1.3%) while the mean is positive (≈ +0.5% to +1.1%):
the edge comes from a minority of large winners.

**One-step go-live for 78 / sector_median (only if chosen):** in `backtest_engine.py` set `WINNER["rs_benchmark"] = "sector_median"`, then
`python run_all.py`. Tag becomes `C6-MED`; the app's rule text follows automatically. Tested in a sandbox copy (pipeline, audit,
app tests, 8599 = 200). Results: `Reports/universe_beta_median_comparison.csv` (the research scripts and charts were moved to the Trash in the 2026-09-25 cleanup; the result CSVs are kept).
