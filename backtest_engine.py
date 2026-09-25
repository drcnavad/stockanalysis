"""
backtest_engine.py
------------------
Point-in-time signal construction + honest portfolio backtests for the stock pipeline.

Rules enforced everywhere:
  * every feature at date t uses only bars <= t (no full-series min/max, no unconfirmed swings)
  * decisions are made at the CLOSE of day t and filled at the OPEN of day t+1
  * 0.1% cost per side on traded notional, no interest on cash
  * partial intraday bars are dropped before backtesting
  * fundamentals / sentiment have no history -> treated as UNAVAILABLE in backtests
  * QQQ / SPY / sector ETFs are benchmarks and inputs, never traded by the strategies

Market data only: uses alpaca_setup.data_client (historical bars). No trading endpoints.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay, USMartinLutherKingJr,
                                    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday, sunday_to_monday)
from pandas.tseries.offsets import CustomBusinessDay

import sector_mapping
import signal_analysis_functions as saf

log = logging.getLogger("backtest_engine")

PROJECT_ROOT = Path(__file__).resolve().parent
REPORTS_DIR = PROJECT_ROOT / "Reports"
CACHE_DIR = REPORTS_DIR / "cache"
EASTERN = ZoneInfo("America/New_York")

COST = 0.001                 # per side
BUY_CUTOFF, SELL_CUTOFF = 20, -25
MIN_BARS = 200               # a stock is eligible once it has a full ma_200 (handles late IPOs consistently)
IS_START, IS_END = "2024-09-17", "2025-09-16"
OOS_START = "2025-09-17"
DATA_START = "2023-06-01"    # warm-up for ma_200 and 252-bar rescaling windows
RS_WINDOWS = (21, 63, 126)
RS_WEIGHTS = {"stock_vs_sector": 0.6, "sector_vs_spy": 0.4}
REGIME_SYMBOL, REGIME_MA = "SPY", 200

# Strategy applied to the live signals.
# v3 (strategy_backtest_v3.ipynb, rolling walk-forward 2022-04 -> now): C6 = v2 winner (weekly top-10 ranking)
# + SOFT regime: at a rebalance where QQQ closes below its 200-day SMA, all weights are halved (rest in cash).
# v4 / sector-cap sweep (2026-09-24, strategy_backtest_v4.ipynb + Reports/strategy_comparison_sector_caps.csv): cap 2 (C6b)
# met the pre-declared rule by a hair (stitched Sharpe 1.48 vs 1.47, max DD -23.7% vs -28.6%) and was live briefly, but the
# user chose to keep cap 4: cap 2 vs 4 is statistically tied (bootstrap P(better) 0.60) and cap 2 lagged badly over the last
# 12 months; caps 5-8 / no cap had higher stitched Sharpe but failed the never-seen 2022-24 test. Buffers, score exits,
# minimum holds, stops and thresholds did not beat the weekly rank rule.
# Selection bias (hand-picked universe) still inflates absolute returns - see Reports/strategy_comparison_v4.csv.
WINNER = {
    "name": "C6: weekly top-10 ranking, max 4 per sector + soft QQQ regime",
    "tag": "C6",
    "n": 10,                # number of names held
    "w_tech": 0.5,          # score = 0.5 * Technical_Score + 0.5 * RS_score
    "use_regime": True,     # regime is used in SOFT mode (see regime_scale)
    "regime_symbol": "QQQ", # regime = QQQ close > its 200-day SMA
    "regime_scale": 0.5,    # regime off at a rebalance -> weights x 0.5 (no hard block on new names)
    "min_score": 0.0,       # only names with score > 0
    "sector_cap": 0.4,      # max 40% of names per sector (= 4 of 10); 0.2 (cap 2, C6b) tested 2026-09-24, not adopted
    "vol_sizing": True,     # inverse 63-day volatility weights
    "buffer_rank": None,    # rank buffer did not help in the walk-forward
    "atr_stop_k": 3.0,      # ATR multiple shown as a risk reference in the app (not an automatic exit)
    "rs_benchmark": "etf",  # relative-strength benchmark: 'etf' (live) | 'sector_median' | 'median_all' (tested 2026-09-24, see relative_strength)
    # Mid-week swap (LIVE since 2026-09-24, user decision; variant D of Reports/rebalance_frequency_test.csv, +438% / Sharpe 1.46
    # vs +391% / 1.40 weekly-only, 2022-04 -> 2026-09, 0.1%/side). At the Mon and Wed closes (first session on/after that
    # weekday if it is a holiday, skipped when it is the week's last session = the Friday rebalance): if a NOT-held stock ranks in
    # the top `enter_top` AND a held stock ranks below `exit_below`, the worst-ranked held stock is sold and the best new
    # entrant bought with the same weight (sector cap respected; repeated while pairs qualify); filled at the next open.
    # REVERT: set "midweek_swap": None and rerun `python run_all.py` -> plain weekly C6-U96 (tag loses "-MW").
    "midweek_swap": {"enter_top": 3, "exit_below": 15, "days": ["Mon", "Wed"]},
    # Mid-week exit (LIVE from 2026-09-25, user decision; variant S3 of Reports/sell_rule_test.csv: +421.6% / Sharpe 1.47 / max DD
    # -32.2% / never-seen Sharpe 0.82 vs plain MW +438.1% / 1.46 / -31.8% / 0.84). At each Mon/Wed check, AFTER the swap step
    # above: every holding ranked worse than this (or no longer qualifying) is sold at the next open and the cash stays idle
    # until the Friday rebalance. Needs "midweek_swap" on. Tag gets "30" (C6-U96-MW30).
    # REVERT: set "midweek_exit_below": None and rerun `python run_all.py` -> plain C6-U96-MW.
    "midweek_exit_below": 30,
    # Selection from ranks 1-20 only (LIVE from 2026-09-25, user decision, not a tested rule). Weekly: walk ranks 1..20 with the
    # max-4-per-sector cap; if fewer than n are filled, fill the rest from the unused ranks 1..20 in rank order IGNORING the
    # sector cap; never pick worse than rank 20; fewer than n qualifying names -> the rest stays cash. Mid-week (cap_soft):
    # a non-held top-3 stock always qualifies regardless of sector and replaces the worst-ranked holding below 15.
    # Tag gets "-T20" (C6-U96-T20-MW30); "-T20H" if cap_soft is False (hard cap inside the top 20), "-SC" for cap_soft alone.
    # REVERT: "max_pick_rank": None and "cap_soft": False, then `python run_all.py` -> C6-U96-MW30.
    "max_pick_rank": 20,
    "cap_soft": True,
}


# Universe label: when the tested universe expansion is switched on (sector_mapping.EXPANDED_UNIVERSE), tracking rows and the
# app say e.g. "C6-U142" so rows from the 78-stock universe stay distinguishable. No effect while the switch is None.
# LIVE since 2026-09-24 (user decision): EXPANDED_UNIVERSE = "u96" -> tag C6-U96 (U91 + CRDO NBIS LITE CLS RBRK; ranks 1-10, max 4 per
# sector, RS vs sector ETF). Before that: "high_beta_91" -> C6-U91, None -> C6 (78 stocks).
if getattr(sector_mapping, "EXPANDED_UNIVERSE", None):
    WINNER["tag"] = f'{WINNER["tag"]}-U{len(sector_mapping.tradable_symbols)}'
    WINNER["name"] = f'{WINNER["name"]} (expanded universe, {len(sector_mapping.tradable_symbols)} stocks)'
if WINNER.get("rs_benchmark", "etf") != "etf":   # median-based relative strength (tested 2026-09-24, not live)
    WINNER["tag"] += {"sector_median": "-MED", "median_all": "-MEDALL"}[WINNER["rs_benchmark"]]
    WINNER["name"] += f' [RS vs {WINNER["rs_benchmark"].replace("_", " ")}]'
if WINNER.get("max_pick_rank") or WINNER.get("cap_soft"):   # picks from ranks 1..20, soft sector cap (live from 2026-09-25)
    WINNER["tag"] += (f'-T{WINNER["max_pick_rank"]}' + ("" if WINNER.get("cap_soft") else "H")) if WINNER.get("max_pick_rank") else "-SC"
    WINNER["name"] += ((f' [picks from ranks 1-{WINNER["max_pick_rank"]} only' if WINNER.get("max_pick_rank") else " [")
                       + ("; sector cap relaxed to fill the 10 slots; top-3 swaps ignore the cap]" if WINNER.get("cap_soft") else "]"))
if WINNER.get("midweek_swap"):                   # mid-week swap on top of the weekly rebalance (live since 2026-09-24)
    _mw = WINNER["midweek_swap"]
    WINNER["tag"] += "-MW"
    WINNER["name"] += (f' + mid-week swap ({"/".join(_mw["days"])} close: top {_mw["enter_top"]} in, '
                       f'below rank {_mw["exit_below"]} out)')
    if WINNER.get("midweek_exit_below"):         # mid-week exit to cash (live from 2026-09-25)
        WINNER["tag"] += str(WINNER["midweek_exit_below"])
        WINNER["name"] += f' + mid-week exit (sell if worse than rank {WINNER["midweek_exit_below"]}, cash until Friday)'


def winner_max_per_sector():
    """Max names per sector implied by WINNER (same formula as rank_targets)."""
    return max(1, int(np.floor(WINNER["sector_cap"] * WINNER["n"])))


def winner_rank_args(regime):
    """kwargs for rank_targets() implementing WINNER."""
    S = WINNER
    return dict(n=S["n"], regime=regime if S["use_regime"] else None, min_score=S["min_score"],
                sector_cap=S["sector_cap"], vol_sizing=S["vol_sizing"], buffer_rank=S["buffer_rank"],
                regime_scale=S["regime_scale"] if S["use_regime"] else None,
                max_pick_rank=S.get("max_pick_rank"), cap_soft=bool(S.get("cap_soft")))


TRADABLE = list(sector_mapping.tradable_symbols)
SECTOR_ETFS = list(sector_mapping.sector_etfs)
BENCHMARKS = list(sector_mapping.BENCHMARK_SYMBOLS)


# ----------------------------------------------------------------------------- calendar
class NYSEHolidayCalendar(AbstractHolidayCalendar):
    """Full-day NYSE holidays (early closes are ignored)."""
    rules = [
        Holiday("NewYearsDay", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr, USPresidentsDay, GoodFriday, USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("IndependenceDay", month=7, day=4, observance=nearest_workday),
        USLaborDay, USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


NYSE_SESSION = CustomBusinessDay(calendar=NYSEHolidayCalendar())


def next_sessions(after, n):
    """The n NYSE sessions strictly after `after`."""
    return pd.date_range(pd.Timestamp(after).normalize() + NYSE_SESSION, periods=n, freq=NYSE_SESSION)


# ----------------------------------------------------------------------------- data
def fetch_daily_bars(symbols, start=DATA_START, end=None, data_client=None, retries=3):
    """Split+dividend adjusted daily bars (long format). Market-data endpoint only."""
    from alpaca.data.enums import Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    if data_client is None:
        from alpaca_setup import data_client
    symbols = list(dict.fromkeys(symbols))
    # Free Alpaca plans cannot query the most recent 15 minutes of SIP data -> stop 16 minutes ago.
    end_ts = pd.Timestamp(end).to_pydatetime() if end else datetime.now(EASTERN) - timedelta(minutes=16)

    def fetch_chunk(chunk):
        """Download one chunk of symbols, retrying on errors."""
        req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
                               start=pd.Timestamp(start).to_pydatetime(), end=end_ts, adjustment=Adjustment.ALL)
        for attempt in range(retries):
            try:
                got = data_client.get_stock_bars(req).df
                return got.reset_index() if got is not None and len(got) else None
            except Exception as e:  # network / rate limit (HTTP 429) -> back off and retry
                if attempt == retries - 1:
                    raise RuntimeError(f"Alpaca bars failed for {chunk[:3]}... after {retries} tries: {e}") from e
                wait = 2 ** attempt * 3
                log.warning("Alpaca bars attempt %d failed (%s); retrying in %ss", attempt + 1, e, wait)
                time.sleep(wait)

    # chunks of 40 symbols, requested in parallel (3 requests for the live universe); results are kept in chunk order
    chunks = [symbols[i:i + 40] for i in range(0, len(symbols), 40)]
    with ThreadPoolExecutor(max_workers=min(4, len(chunks) or 1)) as pool:
        frames = [f for f in pool.map(fetch_chunk, chunks) if f is not None]
    if not frames:
        raise RuntimeError("Alpaca returned no bars at all - check keys / network")
    bars = pd.concat(frames, ignore_index=True).rename(columns={
        "symbol": "Symbol", "timestamp": "Date", "open": "Open", "high": "High",
        "low": "Low", "close": "Close", "volume": "Volume"})
    bars["Date"] = pd.to_datetime(bars["Date"].dt.tz_convert(EASTERN).dt.date)
    missing = sorted(set(symbols) - set(bars["Symbol"]))
    if missing:
        log.warning("No Alpaca bars for: %s", missing)
    bars = apply_history_start(bars)
    return bars[["Symbol", "Date", "Open", "High", "Low", "Close", "Volume"]].sort_values(["Symbol", "Date"]).reset_index(drop=True)


def apply_history_start(bars):
    """Drop vendor bars before sector_mapping.HISTORY_START[symbol] (e.g. NBIS before 2024-10-21 = Yandex N.V. history incl. a flat
    zero-volume 2022-24 halt). Bar counts, MAs and the 200-bar eligibility then start at the first real session: no lookahead."""
    starts = getattr(sector_mapping, "HISTORY_START", {}) or {}
    if not starts or not len(bars):
        return bars
    first = bars["Symbol"].map(lambda s: pd.Timestamp(starts[s]) if s in starts else pd.NaT)
    return bars[first.isna() | (bars["Date"] >= first)].reset_index(drop=True)


def drop_partial_last_bar(bars, now=None, close_buffer_min=30):
    """Drop today's bar if the US session (16:00 ET + buffer) has not finished yet."""
    now = now or datetime.now(EASTERN)
    today = pd.Timestamp(now.date())
    session_done = (now.hour * 60 + now.minute) >= (16 * 60 + close_buffer_min)
    if not session_done and (bars["Date"] == today).any():
        return bars[bars["Date"] < today].reset_index(drop=True), True
    return bars, False


def load_bars(refresh=True, cache_name="bars_daily.pkl", start=DATA_START):
    """All bars needed by the backtest (tradable + benchmarks + sector ETFs), cached under Reports/cache.
    The cache is refetched when asked, when it is missing, or when it starts later than `start`."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / cache_name
    stale = not path.exists() or pd.read_pickle(path)["Date"].min() > pd.Timestamp(start) + pd.Timedelta(days=7)
    if refresh or stale:
        bars = fetch_daily_bars(TRADABLE + BENCHMARKS + SECTOR_ETFS, start=start)
        bars.to_pickle(path)
    bars = apply_history_start(pd.read_pickle(path))
    bars, dropped = drop_partial_last_bar(bars)
    return bars, dropped


# ----------------------------------------------------------------------------- signals
def build_technical(bars, symbols=None):
    """Current technical rules recomputed from raw bars, point-in-time. Returns long df."""
    symbols = symbols or TRADABLE
    b = bars[bars["Symbol"].isin(symbols)]
    df = pd.concat([saf.calculate_technical_indicators(g.reset_index(drop=True)) for _, g in b.groupby("Symbol")],
                   ignore_index=True)
    df["bar_n"] = df.groupby("Symbol").cumcount() + 1
    df = saf.generate_strict_signals(df)
    df = saf.apply_by_symbol(df, saf.rsi_signals)
    df = saf.apply_by_symbol(df, saf.fi_signals_strict)
    df = saf.bollinger_signal_middle(df)
    df = saf.apply_by_symbol(df, saf.macd_signals)
    df = saf.fibonacci_signals(df)
    df = saf.weighted_signal(df).reset_index(drop=True)
    df["Technical_Score"] = pd.to_numeric(df["combined_signal"], errors="coerce").fillna(0.0)
    df["eligible"] = df["bar_n"] >= MIN_BARS
    return df.sort_values(["Symbol", "Date"]).reset_index(drop=True)


def wide(df, col, index="Date", columns="Symbol"):
    """Long table -> wide Date x Symbol matrix of one column (last value per cell)."""
    return df.pivot_table(index=index, columns=columns, values=col, aggfunc="last").sort_index()


def bool_wide(df, col, index, columns):
    """Boolean dates x symbols matrix (missing -> False) without object-dtype downcasting warnings."""
    w = df.pivot_table(index="Date", columns="Symbol", values=col, aggfunc="last").astype(float)
    return w.reindex(index=index, columns=columns).fillna(0.0).astype(bool)


def _peer_median(r, symbols, sector_of, min_peers=3, leave_one_out=True):
    """Per stock: median return of its sector peers inside `symbols` (excluding the stock itself when leave_one_out),
    NaN where fewer than `min_peers` peers have a value on that date (the caller falls back to the sector ETF)."""
    out = {}
    by_sector = {}
    for s in symbols:
        by_sector.setdefault(sector_of.get(s), []).append(s)
    for sec, members in by_sector.items():
        block = r[members]
        for s in members:
            peers = block.drop(columns=[s]) if leave_one_out else block
            med = peers.median(axis=1, skipna=True)
            out[s] = med.where(peers.notna().sum(axis=1) >= min_peers)
    return pd.DataFrame(out, index=r.index)[symbols]


def relative_strength(close_w, symbols=None, vol_adjust=False, benchmark=None):
    """Point-in-time relative strength, all -100..100 cross-sectional scores.

    benchmark (default WINNER['rs_benchmark'], live = 'etf'):
      'etf'           stock vs its SPDR sector ETF; sector ETF vs SPY (original rule)
      'sector_median' stock vs the MEDIAN return of its sector peers in `symbols` (leave-one-out, >= 3 peers with data,
                      otherwise the sector ETF); the sector-vs-SPY half is unchanged (ETF vs SPY)
      'median_all'    as 'sector_median', and the second half = sector median (all members, >= 3) vs the median of the
                      whole universe instead of ETF vs SPY (falls back to ETF vs SPY for small sectors)
    Medians are cross-sectional on each date over the same 21/63/126-day returns, so there is no lookahead.

    stock_vs_sector: stock return - its sector ETF return; sector_vs_spy: sector ETF return - SPY return,
    each over 21/63/126 trading days, converted to cross-sectional percentile ranks per date and blended
    60/40. Also returns Sector_RS_63 (sector ETF 63-day excess return vs SPY, in %) for display.
    """
    symbols = symbols or [s for s in TRADABLE if s in close_w.columns]
    benchmark = benchmark or WINNER.get("rs_benchmark", "etf")
    assert benchmark in ("etf", "sector_median", "median_all"), benchmark
    etf_of = {s: sector_mapping.sector_etf_for(s) for s in symbols}
    parts_sv, parts_ss = [], []
    daily = close_w.pct_change(fill_method=None) if vol_adjust else None
    for w in RS_WINDOWS:
        r = close_w / close_w.shift(w) - 1
        stock = r[symbols]
        sector = pd.DataFrame({s: r[etf_of[s]] if etf_of[s] in r else r["SPY"] for s in symbols})
        if benchmark == "etf":
            sv = stock - sector
            ss = sector.sub(r["SPY"], axis=0)
        else:
            sec_of = sector_mapping.symbol_sector
            sv = stock - _peer_median(r, symbols, sec_of).fillna(sector)
            if benchmark == "sector_median":
                ss = sector.sub(r["SPY"], axis=0)
            else:
                sec_all = _peer_median(r, symbols, sec_of, leave_one_out=False)
                univ_med = stock.median(axis=1, skipna=True)
                ss = sec_all.sub(univ_med, axis=0).fillna(sector.sub(r["SPY"], axis=0))
        if vol_adjust:  # excess return per unit of the stock's own volatility over the same window
            vol = (daily.rolling(w, min_periods=max(10, w // 2)).std() * np.sqrt(w)).replace(0, np.nan)
            sv = sv / vol[symbols]
            sec_vol = pd.DataFrame({s: vol[etf_of[s]] if etf_of[s] in vol else vol["SPY"] for s in symbols})
            ss = ss / sec_vol
        parts_sv.append(sv.rank(axis=1, pct=True))
        parts_ss.append(ss.rank(axis=1, pct=True))
    pct = (RS_WEIGHTS["stock_vs_sector"] * sum(parts_sv) / len(parts_sv)
           + RS_WEIGHTS["sector_vs_spy"] * sum(parts_ss) / len(parts_ss))
    rs_score = (pct * 2 - 1) * 100
    r63 = close_w / close_w.shift(63) - 1
    sector_rs63 = pd.DataFrame({s: (r63[etf_of[s]] if etf_of[s] in r63 else r63["SPY"]) - r63["SPY"] for s in symbols}) * 100
    return rs_score, sector_rs63


def momentum_score(close_w, symbols, sector_neutral=False, lookback=252, skip=21):
    """12-1 month momentum (return from t-252 to t-21), optionally minus the sector ETF's, as a
    cross-sectional -100..100 score."""
    mom = close_w.shift(skip) / close_w.shift(lookback) - 1
    m = mom[symbols]
    if sector_neutral:
        etf = {s: sector_mapping.sector_etf_for(s) for s in symbols}
        m = m - pd.DataFrame({s: mom[etf[s]] if etf[s] in mom else mom["SPY"] for s in symbols})
    return (m.rank(axis=1, pct=True) * 2 - 1) * 100


def regime_series(close_w, symbol=REGIME_SYMBOL, ma=REGIME_MA):
    """Market filter: True while the regime symbol (QQQ) closes above its moving average."""
    c = close_w[symbol]
    return (c > c.rolling(ma).mean()).fillna(False)


# ----------------------------------------------------------------------------- earnings
def load_earnings(path=None):
    """Reports/earnings_date.csv with clean symbols and normalized dates."""
    e = pd.read_csv(path or REPORTS_DIR / "earnings_date.csv")
    e["Symbol"] = e["Symbol"].str.strip().str.upper()
    e["Earnings Date"] = pd.to_datetime(e["Earnings Date"], errors="coerce").dt.normalize()
    return e.dropna(subset=["Earnings Date"])


def _calendar(dates, extra=10):
    """The trading dates plus the next `extra` NYSE sessions (so events just after the data end are placed)."""
    dates = pd.DatetimeIndex(dates)
    return dates.append(next_sessions(dates[-1], extra))


def earnings_matrices(dates, symbols, earnings):
    """Returns (block, reaction, is_earn) boolean DataFrames on `dates` x `symbols`.

    reaction[R, s]: first trading session that can react to the report (AM on E -> E, PM on E -> next session).
    block[d, s]: decision on close d must be FLAT, because the position filled at open d+1 would be held
                 through the reaction gap (close R-1 -> open R), i.e. d == R-2 in trading days.
    is_earn[E, s]: earnings date itself (display flag).
    """
    cal = _calendar(dates)
    idx = {s: i for i, s in enumerate(symbols)}
    T, N = len(dates), len(symbols)
    block = np.zeros((T, N), bool)
    reaction = np.zeros((T, N), bool)
    is_earn = np.zeros((T, N), bool)
    for sym, day, tm in earnings[["Symbol", "Earnings Date", "Time"]].itertuples(index=False):
        if sym not in idx:
            continue
        j = idx[sym]
        k = cal.searchsorted(day)              # first session >= earnings date
        if k >= len(cal):
            continue
        same_day = cal[k] == day
        if tm == "AM":
            r_idx = [k]
        elif tm == "PM":
            r_idx = [k + 1 if same_day else k]
        else:                                  # unknown timing -> be conservative, block both
            r_idx = [k, k + 1]
        if same_day and k < T:
            is_earn[k, j] = True
        for r in r_idx:
            if r < T:
                reaction[r, j] = True
            if 0 <= r - 2 < T:
                block[r - 2, j] = True
    mk = lambda a: pd.DataFrame(a, index=dates, columns=symbols)
    return mk(block), mk(reaction), mk(is_earn)


# ----------------------------------------------------------------------------- simulator
def simulate(open_w, close_w, target, start, end=None, rebalance=None, cost=COST, dd_brake=None, vol_target=None):
    """Share-based daily simulation.

    target: weights decided at the close of each date (row d executes at the open of d+1).
    rebalance: bool Series; True -> trade every symbol to its target; False -> only open new
               positions (0 -> w, sized at w * equity) or close positions (w -> 0), no resizing.
    Accounting starts flat with equity 1.0 just before the open of `start`; the order from the
    previous session's decision (close before `start`) is filled at `start`'s open.
    dd_brake: optional (threshold, scale) - while the strategy's own drawdown at the previous close is
              worse than -threshold, target weights are multiplied by `scale` (drawdown awareness).
    vol_target: optional (annual_vol, lookback) - weights are scaled by min(1, annual_vol / realized
              vol of the strategy's last `lookback` daily returns) (needs lookback days of history).
    """
    dates = open_w.index
    s0 = dates.searchsorted(pd.Timestamp(start))
    s1 = len(dates) if end is None else dates.searchsorted(pd.Timestamp(end), side="right")
    cols = list(open_w.columns)
    O = open_w.to_numpy(float)
    C = close_w.reindex(columns=cols).ffill().to_numpy(float)
    W = target.reindex(index=dates, columns=cols).fillna(0.0).to_numpy(float)
    reb = (rebalance.reindex(dates).astype("boolean").fillna(False).to_numpy(bool) if rebalance is not None
           else np.zeros(len(dates), bool))
    N = len(cols)
    shares = np.zeros(N)
    basis = np.zeros(N)            # cost basis per share incl. buy cost
    entry_day = np.full(N, -1)
    cash = 1.0
    eq, expo, turn = [], [], []
    trades = []
    for t in range(s0, s1):
        o = O[t]
        px_open = np.where(np.isnan(o), C[t - 1] if t > 0 else np.nan, o)
        V = cash + np.nansum(shares * np.nan_to_num(px_open))
        w = W[t - 1] if t > 0 else np.zeros(N)
        if dd_brake is not None and eq:
            peak_eq = max(1.0, max(eq))
            if eq[-1] / peak_eq - 1 < -dd_brake[0]:
                w = w * dd_brake[1]
        if vol_target is not None and len(eq) > vol_target[1]:
            rets = np.diff(np.asarray(eq[-(vol_target[1] + 1):])) / np.asarray(eq[-(vol_target[1] + 1):-1])
            realized = rets.std() * np.sqrt(252)
            if realized > 0:
                w = w * min(1.0, vol_target[0] / realized)
        tradable = ~np.isnan(o)
        if reb[t - 1] if t > 0 else False:
            desired = np.where(tradable, w * V / np.where(tradable, o, 1.0), shares)
        else:
            desired = shares.copy()
            opening = tradable & (shares == 0) & (w > 0)
            closing = tradable & (shares > 0) & (w <= 0)
            desired[opening] = w[opening] * V / o[opening]
            desired[closing] = 0.0
        delta = desired - shares
        delta[np.abs(delta * np.nan_to_num(px_open)) < 1e-10] = 0.0
        traded_notional = 0.0
        # sells first
        for j in np.where(delta < 0)[0]:
            q = -delta[j]
            proceeds = q * o[j] * (1 - cost)
            cash += proceeds
            traded_notional += q * o[j]
            if desired[j] == 0:
                trades.append((cols[j], dates[entry_day[j]], dates[t], basis[j], o[j] * (1 - cost),
                               o[j] * (1 - cost) / basis[j] - 1))
                basis[j] = 0.0
                entry_day[j] = -1
            shares[j] = desired[j]
        buys = np.where(delta > 0)[0]
        need = float(np.sum(delta[buys] * o[buys] * (1 + cost))) if len(buys) else 0.0
        scale = min(1.0, max(cash, 0.0) / need) if need > 0 else 1.0
        for j in buys:
            q = delta[j] * scale
            if q <= 0:
                continue
            cash -= q * o[j] * (1 + cost)
            traded_notional += q * o[j]
            new_sh = shares[j] + q
            basis[j] = (basis[j] * shares[j] + q * o[j] * (1 + cost)) / new_sh
            if shares[j] == 0:
                entry_day[j] = t
            shares[j] = new_sh
        pos_val = np.nansum(shares * C[t])
        e = cash + pos_val
        eq.append(e)
        expo.append(pos_val / e if e > 0 else 0.0)
        turn.append(traded_notional / V if V > 0 else 0.0)
    idx = dates[s0:s1]
    trades = pd.DataFrame(trades, columns=["Symbol", "Entry", "Exit", "EntryPx", "ExitPx", "Return"])
    open_pos = pd.DataFrame({"Symbol": [cols[j] for j in np.where(shares > 0)[0]],
                             "Entry": [dates[entry_day[j]] for j in np.where(shares > 0)[0]]})
    return {"equity": pd.Series(eq, idx), "exposure": pd.Series(expo, idx), "turnover": pd.Series(turn, idx),
            "trades": trades, "open_positions": open_pos}


def metrics(res, name=None):
    """Summary statistics of one simulation (return, CAGR, Sharpe, drawdown, turnover, trade stats)."""
    eq, ex = res["equity"], res["exposure"]
    r = eq.pct_change()
    r.iloc[0] = eq.iloc[0] - 1
    n = len(eq)
    years = n / 252
    total = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else np.nan
    vol = r.std()
    downside = np.sqrt((np.minimum(r, 0) ** 2).mean())
    dd = (eq / eq.cummax().clip(lower=1.0) - 1).min()
    closed = res["trades"]
    opened = len(closed) + len(res["open_positions"])
    out = {
        "Strategy": name,
        "Start": eq.index[0].date(), "End": eq.index[-1].date(), "Days": n,
        "Total Return %": total * 100,
        "CAGR %": cagr * 100,
        "Sharpe": r.mean() / vol * np.sqrt(252) if vol > 0 else np.nan,
        "Sortino": r.mean() / downside * np.sqrt(252) if downside > 0 else np.nan,
        "Max DD %": dd * 100,
        "Calmar": cagr / abs(dd) if dd < 0 else np.nan,
        "Exposure %": ex.mean() * 100,
        "Time in Market %": (ex > 0.001).mean() * 100,
        "Return per Invested Day (bp)": (r.mean() / ex.mean() * 1e4) if ex.mean() > 0 else np.nan,
        "Turnover x/yr": res["turnover"].sum() / years,
        "Trades": opened,
        "Closed Trades": len(closed),
        "Win Rate % (closed, net)": (closed["Return"] > 0).mean() * 100 if len(closed) else np.nan,
        "Avg Trade % (closed, net)": closed["Return"].mean() * 100 if len(closed) else np.nan,
    }
    return out


# ----------------------------------------------------------------------------- strategy builders
def threshold_states(score_w, eligible_w, close_w, atr_w, buy=BUY_CUTOFF, sell=SELL_CUTOFF,
                     regime=None, atr_k=None):
    """Per-stock state machine evaluated at each close (1 = want to hold).

    Enter when score > buy (and eligible, and regime on if given, and - after a stop-out - only on a
    fresh BUY, i.e. the score must first fall back to <= buy). Exit when score < sell or, if atr_k is
    set, when close < highest close since entry - atr_k * ATR(14).
    """
    S = score_w.to_numpy(float)
    E = eligible_w.reindex_like(score_w).astype("boolean").fillna(False).to_numpy(bool)
    Cl = close_w.reindex_like(score_w).to_numpy(float)
    A = atr_w.reindex_like(score_w).to_numpy(float)
    R = (regime.reindex(score_w.index).astype("boolean").fillna(False).to_numpy(bool) if regime is not None
         else np.ones(len(score_w), bool))
    T, N = S.shape
    state = np.zeros(N, bool)
    fresh_ok = np.ones(N, bool)
    peak = np.full(N, np.nan)
    out = np.zeros((T, N))
    for t in range(T):
        s = S[t]
        valid = E[t] & ~np.isnan(s) & ~np.isnan(Cl[t])
        fresh_ok |= (s <= buy)
        exit_sig = state & (s < sell)
        if atr_k is not None:
            peak = np.where(state, np.fmax(peak, Cl[t]), np.nan)
            stop = state & (Cl[t] < peak - atr_k * A[t])
            fresh_ok[stop] = False
            exit_sig |= stop
        state = state & ~exit_sig
        enter = ~state & valid & (s > buy) & R[t] & fresh_ok
        state = state | enter
        if atr_k is not None:
            peak = np.where(enter, Cl[t], peak)
        out[t] = state
    return pd.DataFrame(out, index=score_w.index, columns=score_w.columns)


def cap_positions(states, score_w, k):
    """Hold at most k names: keep current holdings while wanted, fill free slots by highest score."""
    St = states.to_numpy(bool)
    S = score_w.reindex_like(states).to_numpy(float)
    T, N = St.shape
    held = np.zeros(N, bool)
    out = np.zeros((T, N))
    for t in range(T):
        held &= St[t]
        free = k - held.sum()
        if free > 0:
            cand = np.where(St[t] & ~held)[0]
            if len(cand):
                cand = cand[np.argsort(-np.nan_to_num(S[t, cand], nan=-1e9))][:free]
                held[cand] = True
        out[t] = held
    return pd.DataFrame(out, index=states.index, columns=states.columns)


def _name_positions(cols):
    """Alphabetical position of each column name (final, deterministic tie-break)."""
    pos = np.empty(len(cols), int)
    pos[np.argsort(np.array([str(c) for c in cols]))] = np.arange(len(cols))
    return pos


def ranking_order(idx, scores, tiebreak, name_pos, decimals=6):
    """Indices idx sorted best-first: score (rounded to `decimals`) desc, then tiebreak desc, then name A-Z.

    Rounding removes float noise (0.5*a + 0.5*b can differ in the last bit for mathematically equal scores),
    so exact ties are broken by a documented rule instead of by floating-point accident or sort instability.
    """
    idx = np.asarray(idx, int)
    if len(idx) == 0:
        return idx
    s = np.round(scores[idx], decimals)
    tb = np.zeros(len(idx)) if tiebreak is None else np.nan_to_num(np.round(tiebreak[idx], decimals), nan=-np.inf)
    return idx[np.lexsort((name_pos[idx], -tb, -s))]


def deterministic_rank(score_w, eligible_w=None, tiebreak_w=None):
    """Per-date rank 1..N (1 = best) over eligible symbols with a score; NaN otherwise. Unique ranks, same order
    as rank_targets (score desc at 6 decimals, then tiebreak desc, then name A-Z). Computed date by date."""
    s = score_w.where(eligible_w.reindex_like(score_w).astype("boolean").fillna(False)) if eligible_w is not None else score_w
    S = s.to_numpy(float)
    TB = tiebreak_w.reindex_like(score_w).to_numpy(float) if tiebreak_w is not None else None
    name_pos = _name_positions(list(score_w.columns))
    out = np.full(S.shape, np.nan)
    for t in range(len(S)):
        o = ranking_order(np.flatnonzero(~np.isnan(S[t])), S[t], None if TB is None else TB[t], name_pos)
        out[t, o] = np.arange(1, len(o) + 1)
    return pd.DataFrame(out, index=score_w.index, columns=score_w.columns)


def rank_targets(score_w, eligible_w, vol_w, n=10, regime=None, rebalance_days=None, min_score=0.0,
                 sector_cap=0.4, vol_sizing=True, buffer_rank=None, regime_scale=None, decision_log=None,
                 tiebreak_w=None, max_pick_rank=None, cap_soft=False):
    """Weekly top-N by score with sector cap and inverse-volatility weights.

    On rebalance days: candidates = eligible & score > min_score, best first, at most floor(sector_cap*n)
    per sector. If the regime is off, no NEW names are bought (held names that still qualify are kept).
    Total exposure = (#selected / n); within that, weights ~ 1/vol (63-day). Between rebalances the
    target is carried forward unchanged.
    buffer_rank: keep a current holding while its rank among qualifying names is <= buffer_rank
                 (cuts turnover); free slots are then filled best-first.
    regime_scale: if given, a regime-off rebalance still selects normally but scales weights by this
                  factor (soft regime) instead of blocking new names.
    decision_log: optional list; one dict per symbol per rebalance with status and reason.
    tiebreak_w: optional frame used to order equal scores (higher first); remaining ties go alphabetically.
                Scores are compared at 6 decimals so float noise never decides the order (see ranking_order).
    max_pick_rank: only names ranked 1..max_pick_rank may be picked (T20: 20); fewer than n candidates -> the rest is cash.
    cap_soft: after the capped walk, fill any free slots from the unused candidates in rank order ignoring the sector cap.
    """
    dates, cols = score_w.index, list(score_w.columns)
    S = score_w.to_numpy(float)
    E = eligible_w.reindex_like(score_w).astype("boolean").fillna(False).to_numpy(bool)
    Vv = vol_w.reindex_like(score_w).to_numpy(float)
    TB = tiebreak_w.reindex_like(score_w).to_numpy(float) if tiebreak_w is not None else None
    name_pos = _name_positions(cols)
    R = (regime.reindex(dates).astype("boolean").fillna(False).to_numpy(bool) if regime is not None else np.ones(len(dates), bool))
    reb = rebalance_days.reindex(dates).astype("boolean").fillna(False).to_numpy(bool)
    sectors = np.array([sector_mapping.symbol_sector.get(c, "Other") for c in cols])
    max_per_sector = max(1, int(np.floor(sector_cap * n)))
    out = np.zeros((len(dates), len(cols)))
    current = np.zeros(len(cols))
    for t in range(len(dates)):
        if reb[t]:
            base_ok = E[t] & ~np.isnan(S[t]) & ~np.isnan(Vv[t]) & (Vv[t] > 0)
            ok = base_ok & (S[t] > min_score)
            soft = regime_scale is not None
            if not R[t] and not soft:
                ok &= current > 0
            order = ranking_order(np.where(ok)[0], S[t], None if TB is None else TB[t], name_pos)
            rank_of = {j: r + 1 for r, j in enumerate(order)}
            picked, per_sector, reason = [], {}, {}
            if buffer_rank is not None:  # keep holdings still within the buffer first
                for j in order:
                    if current[j] > 0 and rank_of[j] <= buffer_rank and len(picked) < n \
                            and per_sector.get(sectors[j], 0) < max_per_sector:
                        picked.append(j)
                        per_sector[sectors[j]] = per_sector.get(sectors[j], 0) + 1
                        reason[j] = f"kept (rank {rank_of[j]} <= buffer {buffer_rank})"
            cands = order if max_pick_rank is None else order[:max_pick_rank]
            for j in cands:
                if len(picked) >= n:
                    break
                if j in reason:
                    continue
                if per_sector.get(sectors[j], 0) >= max_per_sector:
                    reason[j] = f"skipped: sector cap ({sectors[j]} already {max_per_sector})"
                    continue
                picked.append(j)
                per_sector[sectors[j]] = per_sector.get(sectors[j], 0) + 1
                reason[j] = f"selected (rank {rank_of[j]})"
            if cap_soft:                   # free slots left: unused candidates in rank order, sector cap ignored
                for j in cands:
                    if len(picked) >= n:
                        break
                    if j in picked:
                        continue
                    picked.append(j)
                    per_sector[sectors[j]] = per_sector.get(sectors[j], 0) + 1
                    reason[j] = (f"selected (rank {rank_of[j]}; sector cap relaxed: fewer than {n} fit the cap within the top "
                                 f"{max_pick_rank or len(cands)})")
            if max_pick_rank is not None:
                for j in order[max_pick_rank:]:
                    if j not in reason and current[j] > 0:
                        reason[j] = f"rank {rank_of[j]} worse than {max_pick_rank} (picks only from ranks 1-{max_pick_rank})"
            new = np.zeros(len(cols))
            if picked:
                inv = 1 / Vv[t, picked] if vol_sizing else np.ones(len(picked))
                new[picked] = inv / inv.sum() * (len(picked) / n)
                if soft and not R[t]:
                    new *= regime_scale
            if decision_log is not None:
                for j in range(len(cols)):
                    if new[j] == 0 and current[j] == 0 and j not in reason:
                        continue
                    if new[j] > 0:
                        status = "hold" if current[j] > 0 else "add"
                        why = reason.get(j, "selected")
                    else:
                        status = "drop" if current[j] > 0 else "not selected"
                        if not base_ok[j]:
                            why = "not eligible / no data"
                        elif not (S[t, j] > min_score):
                            why = f"score {S[t, j]:.1f} <= {min_score:g}"
                        elif not R[t] and not soft and current[j] == 0:
                            why = "regime off: no new names"
                        elif j in reason:
                            why = reason[j]
                        else:
                            why = f"rank {rank_of.get(j, '-')} outside top {n}" + (f" / buffer {buffer_rank}" if buffer_rank else "")
                    decision_log.append({"Date": dates[t], "Symbol": cols[j], "Status": status, "Reason": why,
                                         "Rank": rank_of.get(j, np.nan), "Score": S[t, j], "Sector": sectors[j],
                                         "Old_Weight": current[j], "New_Weight": new[j]})
            current = new
        out[t] = current
    return pd.DataFrame(out, index=dates, columns=cols)


def weekly_rebalance_days(dates, live=False):
    """Last trading day of each ISO week (decision at that close, fill next open).

    live=True: the current (possibly unfinished) week only counts as rebalanced if its latest
    session is a Friday, so a Thursday run does not pretend the week is over.
    """
    d = pd.Series(dates, index=dates)
    wk = d.dt.isocalendar()
    key = wk["year"].astype(str) + "-" + wk["week"].astype(str)
    out = d.groupby(key.values).transform("max").eq(d)
    if live and len(d):
        last = d.iloc[-1]
        nxt = next_sessions(last, 1)[0]
        if nxt.isocalendar()[:2] == last.isocalendar()[:2]:  # another session left this week -> not yet
            out.iloc[-1] = False
    return out


WEEKDAY_CODES = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4}


def midweek_check_days(dates, days=("Mon", "Wed"), rebalance_days=None):
    """Sessions with a mid-week swap check (decision at that close, fill next open).

    Each calendar Mon / Wed (``days``) maps to the first session on or after it, so a Monday holiday moves the check to
    Tuesday. Sessions that are also a weekly rebalance day are excluded (the full rebalance wins). Same mapping as
    the tested variant D (Reports/rebalance_frequency_test.csv; reproduced by tests/test_midweek_repro.py).
    """
    dates = pd.DatetimeIndex(dates)
    if rebalance_days is None:
        rebalance_days = weekly_rebalance_days(dates, live=True)
    if not len(dates):
        return pd.Series(False, index=dates)
    cal = pd.date_range(dates[0], dates[-1], freq="D")
    cal = cal[cal.dayofweek.isin([WEEKDAY_CODES[d] for d in days])]
    mapped = {dates[p] for p in dates.searchsorted(cal) if p < len(dates)}
    weekly = rebalance_days.reindex(dates).astype("boolean").fillna(False).to_numpy(bool)
    return pd.Series(dates.isin(list(mapped)) & ~weekly, index=dates)


def midweek_swap_pairs(cur, order, rank, sectors, enter_top=3, exit_below=15, cap=4):
    """The mid-week swap rule on one check day (used by apply_midweek_swaps and by holdings_alert for real positions).

    cur: weight array (modified in place: each entrant takes the sold holding's weight); order: qualifying column indices,
    best first; rank: {index: rank}; sectors: sector per column. While a held name ranks worse than exit_below (or has no
    rank) and a NOT-held name is in order[:enter_top], take the best entrant and the worst-ranked holding whose removal
    leaves the entrant's sector below cap (ties among unranked holdings: column order). Returns [(entrant, sold, weight)].
    """
    swaps = []
    while True:
        held = np.where(cur > 0)[0]
        weak = sorted([j for j in held if rank.get(j, 1e6) > exit_below], key=lambda j: -rank.get(j, 1e6))
        if not weak:
            break
        entrants = [j for j in order[:enter_top] if cur[j] == 0]
        done = False
        for e in entrants:
            for h in weak:
                in_sector = sum(1 for k in np.where(cur > 0)[0] if k != h and sectors[k] == sectors[e])
                if in_sector < cap:
                    swaps.append((e, h, cur[h]))
                    cur[e], cur[h] = cur[h], 0.0
                    done = True
                    break
            if done:
                break
        if not done:
            break
    return swaps


def midweek_exit_sells(cur, rank, exit_all_below):
    """The mid-week exit rule on one check day, applied AFTER midweek_swap_pairs (used by apply_midweek_swaps and by
    holdings_alert). Every holding ranked worse than exit_all_below (or with no rank) is sold; the cash stays idle until the
    next weekly rebalance. cur is modified in place. Returns [(sold, weight)] in column order."""
    if not exit_all_below:
        return []
    sells = [(j, cur[j]) for j in np.where(cur > 0)[0] if rank.get(j, 1e6) > exit_all_below]
    for j, _ in sells:
        cur[j] = 0.0
    return sells


def apply_midweek_swaps(base, score_w, eligible_w, vol_w, rebalance_days, check_days, enter_top=3, exit_below=15,
                        sector_cap=0.4, n=10, min_score=0.0, tiebreak_w=None, decision_log=None, check_log=None,
                        exit_all_below=None, cap_soft=False):
    """Weekly targets (``base`` from rank_targets) + mid-week swaps on ``check_days``.

    On a check day: rank = position among qualifying names (eligible, score > min_score, valid vol), same order as
    rank_targets (score at 6 decimals, then tiebreak, then name). While some held name ranks worse than ``exit_below``
    (or no longer qualifies) and some NOT-held name ranks in the top ``enter_top``: take the best entrant and the
    worst-ranked held name whose removal leaves room in the entrant's sector (max floor(sector_cap*n)); the entrant takes
    the held name's weight. Repeated until no pair qualifies. Weekly rebalance days reset to ``base``.
    Ties: several held names that no longer qualify (no rank) are taken in column order (= sector_mapping.tradable_symbols),
    exactly as in the tested variant D.
    exit_all_below (mid-week exit, e.g. 30): after the swaps, every holding ranked worse than this (or unranked) is sold and
    its weight stays in cash until the next weekly rebalance (variant S3 of Reports/sell_rule_test.csv).
    cap_soft (T20): the sector cap is ignored at mid-week swaps - a non-held top-3 stock always replaces the worst-ranked
    holding below exit_below.
    decision_log: rows (like rank_targets) for check days WITH a swap or exit (hold / add / drop).
    check_log: one dict per check day and swap (Action 'SWAP') and per exit (Action 'SELL'), or one 'NO SWAP' row with a Note.
    """
    dates, cols = base.index, list(base.columns)
    S = score_w.reindex(index=dates, columns=cols).to_numpy(float)
    E = eligible_w.reindex(index=dates, columns=cols).astype("boolean").fillna(False).to_numpy(bool)
    Vv = vol_w.reindex(index=dates, columns=cols).to_numpy(float)
    TB = tiebreak_w.reindex(index=dates, columns=cols).to_numpy(float) if tiebreak_w is not None else None
    B = base.to_numpy(float)
    reb = rebalance_days.reindex(dates).astype("boolean").fillna(False).to_numpy(bool)
    chk = check_days.reindex(dates).astype("boolean").fillna(False).to_numpy(bool)
    name_pos = _name_positions(cols)
    sectors = np.array([sector_mapping.symbol_sector.get(c, "Other") for c in cols])
    cap = 10 ** 6 if cap_soft else max(1, int(np.floor(sector_cap * n)))
    out = np.zeros_like(B)
    cur = np.zeros(len(cols))
    by_date = {}
    if decision_log is not None:                  # weekly rows written by rank_targets, to re-base them on the swapped holdings
        for k, row in enumerate(decision_log):
            by_date.setdefault(row["Date"], []).append(k)
    for t in range(len(dates)):
        if reb[t]:
            if decision_log is not None and t > 0 and not np.array_equal(cur, B[t - 1]):
                _rebase_weekly_log(decision_log, dates[t], by_date.get(dates[t], []), cur, B[t], cols, sectors, S[t], E[t], Vv[t],
                                   None if TB is None else TB[t], name_pos, min_score, n)
            cur = B[t].copy()
        elif chk[t] and cur.sum() > 0:
            ok = E[t] & ~np.isnan(S[t]) & ~np.isnan(Vv[t]) & (Vv[t] > 0) & (S[t] > min_score)
            order = ranking_order(np.where(ok)[0], S[t], None if TB is None else TB[t], name_pos)
            rank = {j: r + 1 for r, j in enumerate(order)}
            before = cur.copy()
            swaps = midweek_swap_pairs(cur, order, rank, sectors, enter_top, exit_below, cap)
            exits = midweek_exit_sells(cur, rank, exit_all_below)
            if check_log is not None:
                if swaps or exits:
                    for e, h, w in swaps:
                        check_log.append({"Date": dates[t], "Action": "SWAP", "Sell": cols[h], "Sell_Rank": rank.get(h, np.nan),
                                          "Sell_Score": S[t, h], "Buy": cols[e], "Buy_Rank": rank[e], "Buy_Score": S[t, e],
                                          "Weight": w, "Sell_Sector": sectors[h], "Buy_Sector": sectors[e], "Note": ""})
                    for h, w in exits:
                        check_log.append({"Date": dates[t], "Action": "SELL", "Sell": cols[h], "Sell_Rank": rank.get(h, np.nan),
                                          "Sell_Score": S[t, h], "Buy": "", "Buy_Rank": np.nan, "Buy_Score": np.nan,
                                          "Weight": w, "Sell_Sector": sectors[h], "Buy_Sector": "",
                                          "Note": f"worse than rank {exit_all_below}: sold, cash until the weekly rebalance"})
                else:
                    held = np.where(before > 0)[0]
                    worst = max((rank.get(j, 1e6) for j in held), default=np.nan)
                    entrants = [cols[j] for j in order[:enter_top] if before[j] == 0]
                    if not entrants:
                        note = f"all top-{enter_top} stocks are already held"
                    elif worst <= exit_below:
                        note = (f"no held stock is below rank {exit_below} (worst held rank {int(worst)}; "
                                f"new top-{enter_top}: {', '.join(entrants)})")
                    else:
                        weak_txt = ", ".join(f"{cols[j]} {'rank ' + str(rank[j]) if j in rank else 'no longer qualifies'}"
                                             for j in sorted(held, key=lambda k: rank.get(k, 1e6)) if rank.get(j, 1e6) > exit_below)
                        ent_txt = ", ".join(f"{cols[j]} rank {rank[j]} {sectors[j]}" for j in order[:enter_top] if before[j] == 0)
                        note = (f"max {cap} per sector blocks it: new top-{enter_top} {ent_txt} - that sector already has {cap} "
                                f"holdings and the weak holdings below rank {exit_below} ({weak_txt}) are in other sectors")
                    if exit_all_below:
                        note += f"; no holding is worse than rank {exit_all_below}"
                    check_log.append({"Date": dates[t], "Action": "NO SWAP", "Sell": "", "Sell_Rank": np.nan,
                                      "Sell_Score": np.nan, "Buy": "", "Buy_Rank": np.nan, "Buy_Score": np.nan,
                                      "Weight": np.nan, "Sell_Sector": "", "Buy_Sector": "", "Note": note})
            if (swaps or exits) and decision_log is not None:
                partner = {h: e for e, h, _ in swaps}
                partner.update({e: h for e, h, _ in swaps})
                exited = {h for h, _ in exits}
                for j in np.where((before > 0) | (cur > 0))[0]:
                    r = rank.get(j, np.nan)
                    rtxt = f"rank {r}" if r == r else "no longer qualifies"
                    p = partner.get(j)
                    if cur[j] > 0 and before[j] == 0:
                        status = "add"
                        why = (f"mid-week swap in: {rtxt} is in the top {enter_top}; replaces {cols[p]} "
                               f"({'rank ' + str(rank[p]) if p in rank else 'no longer qualifies'})")
                    elif cur[j] == 0 and j in exited:
                        status = "drop"
                        why = (f"mid-week exit: {rtxt} (worse than {exit_all_below}); sold, cash until the weekly rebalance"
                               if r == r else "mid-week exit: no longer qualifies (score <= 0 or no data); sold, cash until "
                               "the weekly rebalance")
                    elif cur[j] == 0:
                        status = "drop"
                        why = f"mid-week swap out: {rtxt} (below {exit_below}); replaced by {cols[p]} (rank {rank[p]})"
                    else:
                        status, why = "hold", f"kept at mid-week check ({rtxt})"
                    decision_log.append({"Date": dates[t], "Symbol": cols[j], "Status": status, "Reason": why,
                                         "Rank": r, "Score": S[t, j], "Sector": sectors[j],
                                         "Old_Weight": before[j], "New_Weight": cur[j]})
        out[t] = cur
    return pd.DataFrame(out, index=dates, columns=cols)


def _rebase_weekly_log(decision_log, date, idx, held_before, new, cols, sectors, s_t, e_t, v_t, tb_t, name_pos, min_score, n):
    """After mid-week swaps the holdings going into a weekly rebalance differ from rank_targets' own carry-forward: fix that
    day's rows (Old_Weight, add/hold/drop) so the log compares with what is really held; add rows for held names it skipped."""
    ok = e_t & ~np.isnan(s_t) & ~np.isnan(v_t) & (v_t > 0) & (s_t > min_score)
    rank = {j: r + 1 for r, j in enumerate(ranking_order(np.where(ok)[0], s_t, tb_t, name_pos))}
    pos = {c: j for j, c in enumerate(cols)}
    seen = set()
    for k in idx:
        row = decision_log[k]
        j = pos[row["Symbol"]]
        seen.add(j)
        old_w = held_before[j]
        if row["Old_Weight"] == old_w:
            continue
        row["Old_Weight"] = old_w
        if row["New_Weight"] > 0:
            row["Status"] = "hold" if old_w > 0 else "add"
        elif old_w > 0:
            row["Status"] = "drop"
            if not str(row["Reason"]).startswith(("score", "not eligible", "skipped", "rank")):
                row["Reason"] = f"rank {rank.get(j, '-')} outside top {n}"
        else:
            row["Status"] = "not selected"
    for j in np.where(held_before > 0)[0]:
        if j in seen:
            continue
        if not (e_t[j] and not np.isnan(s_t[j])):
            why = "not eligible / no data"
        elif not s_t[j] > min_score:
            why = f"score {s_t[j]:.1f} <= {min_score:g}"
        else:
            why = f"rank {rank.get(j, '-')} outside top {n}"
        decision_log.append({"Date": date, "Symbol": cols[j], "Status": "drop",
                             "Reason": why, "Rank": rank.get(j, np.nan), "Score": s_t[j], "Sector": sectors[j],
                             "Old_Weight": held_before[j], "New_Weight": new[j]})


def winner_targets(score_w, eligible_w, vol_w, regime, rebalance_days, tiebreak_w=None, decision_log=None,
                   check_log=None, midweek=None, exit_all_below="winner", selection=None):
    """Live WINNER targets: weekly rank_targets + (if WINNER['midweek_swap']) mid-week swaps + (if
    WINNER['midweek_exit_below']) mid-week exits to cash.

    Returns (targets, check_days). check_days is all-False when the mid-week swap is off. ``midweek`` overrides
    WINNER['midweek_swap'] (pass False to force plain weekly); ``exit_all_below`` overrides WINNER['midweek_exit_below']
    (None = no mid-week exit); ``selection`` = dict(max_pick_rank=..., cap_soft=...) overrides the T20 keys."""
    mw = WINNER.get("midweek_swap") if midweek is None else midweek
    if exit_all_below == "winner":
        exit_all_below = WINNER.get("midweek_exit_below")
    args = winner_rank_args(regime)
    args.update(selection or {})
    base = rank_targets(score_w, eligible_w, vol_w, rebalance_days=rebalance_days, decision_log=decision_log,
                        tiebreak_w=tiebreak_w, **args)
    if not mw:
        return base, pd.Series(False, index=base.index)
    checks = midweek_check_days(base.index, mw.get("days", ("Mon", "Wed")), rebalance_days)
    tgt = apply_midweek_swaps(base, score_w, eligible_w, vol_w, rebalance_days, checks, mw["enter_top"], mw["exit_below"],
                              WINNER["sector_cap"], WINNER["n"], WINNER["min_score"], tiebreak_w=tiebreak_w,
                              decision_log=decision_log, check_log=check_log, exit_all_below=exit_all_below,
                              cap_soft=args["cap_soft"])
    return tgt, checks


def next_decision(latest, days=None):
    """Next decision session after ``latest``: (date, 'full rebalance' | 'mid-week check', fill_date).

    Weekly rebalance = last session of an ISO week; mid-week check = first session on/after each Mon / Wed that is not
    the week's last session (same rules as weekly_rebalance_days / midweek_check_days)."""
    mw = WINNER.get("midweek_swap")
    if days is None:
        days = mw.get("days", ("Mon", "Wed")) if mw else ()
    codes = {WEEKDAY_CODES[d] for d in days}
    latest = pd.Timestamp(latest).normalize()
    sess = [latest] + list(next_sessions(latest, 12))
    for i in range(1, len(sess) - 1):
        d, prev = sess[i], sess[i - 1]
        if d.isocalendar()[:2] != sess[i + 1].isocalendar()[:2]:
            return d, "full rebalance", sess[i + 1]
        if any(c.dayofweek in codes for c in pd.date_range(prev + pd.Timedelta(days=1), d, freq="D")):
            return d, "mid-week check", sess[i + 1]
    return sess[1], "full rebalance", sess[2]


def monthly_rebalance_days(dates):
    """Last trading day of each calendar month."""
    d = pd.Series(dates, index=dates)
    return d.groupby(d.dt.to_period("M").values).transform("max").eq(d)


def pead_targets(reaction_w, close_w, open_w, threshold=0.05, hold=20, k=10, block=None):
    """Post-earnings drift: if the reaction session return (close R / close R-1 - 1) >= threshold,
    buy at the next open (decision at close R) and hold `hold` sessions; max k names, 1/k each."""
    ret = close_w / close_w.shift(1) - 1
    trig = (reaction_w.reindex_like(close_w).astype("boolean").fillna(False).astype(bool) & (ret >= threshold)).to_numpy(bool)
    T, N = trig.shape
    Bk = block.reindex_like(close_w).astype("boolean").fillna(False).to_numpy(bool) if block is not None else np.zeros((T, N), bool)
    age = np.full(N, -1)
    out = np.zeros((T, N))
    R_ = ret.to_numpy(float)
    for t in range(T):
        held = age >= 0
        age[held] += 1
        age[(age >= hold) | (held & Bk[t])] = -1
        free = k - (age >= 0).sum()
        cand = np.where(trig[t] & (age < 0))[0]
        if free > 0 and len(cand):
            cand = cand[np.argsort(-R_[t, cand])][:free]
            age[cand] = 0
        out[t] = (age >= 0) / k
    return pd.DataFrame(out, index=close_w.index, columns=close_w.columns)


def buy_and_hold_target(close_w, symbols, start):
    """Equal weight on the session before `start` (filled at start's open), never rebalanced."""
    dates = close_w.index
    d0 = dates[max(dates.searchsorted(pd.Timestamp(start)) - 1, 0)]
    alive = [s for s in symbols if pd.notna(close_w.at[d0, s])]
    t = pd.DataFrame(0.0, index=dates, columns=close_w.columns)
    t.loc[d0:, alive] = 1.0 / len(alive)
    return t, alive


# ----------------------------------------------------------------------------- overlays
def atr_stop_overlay(target, close_w, atr_w, rebalance_days, k=3.0):
    """Exit a holding when close < highest close since entry - k*ATR; stay out until the next rebalance."""
    cols = list(target.columns)
    W = target.to_numpy(float).copy()
    Cl = close_w.reindex(index=target.index, columns=cols).to_numpy(float)
    A = atr_w.reindex(index=target.index, columns=cols).to_numpy(float)
    reb = rebalance_days.reindex(target.index).astype("boolean").fillna(False).to_numpy(bool)
    peak = np.full(len(cols), np.nan)
    stopped = np.zeros(len(cols), bool)
    for t in range(len(W)):
        if reb[t]:
            stopped[:] = False
        held = (W[t] > 0) & ~stopped
        peak = np.where(held, np.fmax(peak, Cl[t]), np.nan)
        hit = held & (Cl[t] < peak - k * A[t])
        stopped |= hit
        W[t, stopped] = 0.0
    return pd.DataFrame(W, index=target.index, columns=cols)


def blend_with_core(target, core_symbol="QQQ", core_weight=0.5, columns=None):
    """(1 - core_weight) x strategy + core_weight in a core ETF (e.g. 50% QQQ + 50% strategy)."""
    cols = columns if columns is not None else list(target.columns) + [core_symbol]
    out = target.reindex(columns=cols).fillna(0.0) * (1 - core_weight)
    out[core_symbol] = out.get(core_symbol, 0.0) + core_weight
    return out


# ----------------------------------------------------------------------------- live helpers
def holding_details(weights, open_w, close_w, atr_w, vol_w, k=3.0):
    """Per current holding: decision/fill dates, entry price (next open after the decision), days held,
    P&L since entry, 63d annualized volatility, ATR and an ATR trailing-stop level."""
    last = weights.index[-1]
    rows = []
    for sym in weights.columns[weights.loc[last] > 0]:
        w = weights[sym]
        off = w[w <= 0]
        run_start = w.index[w.index > off.index[-1]][0] if len(off) else w.index[0]
        fills = open_w.index[open_w.index > run_start]
        fill = fills[0] if len(fills) else None
        entry = float(open_w.at[fill, sym]) if fill is not None and pd.notna(open_w.at[fill, sym]) else np.nan
        close = float(close_w.at[last, sym])
        since = close_w.loc[fill:, sym] if fill is not None else pd.Series(dtype=float)
        peak = since.max() if len(since) else np.nan
        atr = float(atr_w.at[last, sym]) if sym in atr_w else np.nan
        rows.append({"Symbol": sym, "Weight": float(w.loc[last]), "Decision_Date": run_start.date(),
                     "Entry_Date": fill.date() if fill is not None else None, "Entry_Price": entry,
                     "Close": close, "PnL_%": (close / entry - 1) * 100 if entry == entry else np.nan,
                     "Days_Held": int(len(since)) if len(since) else 0,
                     "Vol_63d_%": float(vol_w.at[last, sym]) * np.sqrt(252) * 100 if sym in vol_w else np.nan,
                     "ATR": atr, "ATR_Stop": peak - k * atr if peak == peak else np.nan,
                     "Dist_to_Stop_%": (close / (peak - k * atr) - 1) * 100 if peak == peak else np.nan})
    return pd.DataFrame(rows)


def forward_tracking(target, open_w, close_w, first_decision, benchmarks=("QQQ", "SPY"), rebalance=None):
    """Equity of the live target vs benchmarks, starting with the fill after `first_decision`."""
    start_candidates = open_w.index[open_w.index > pd.Timestamp(first_decision)]
    if not len(start_candidates):
        return pd.DataFrame()
    start = start_candidates[0]
    tgt = target.reindex(index=close_w.index, columns=close_w.columns).fillna(0.0)
    res = simulate(open_w, close_w, tgt, start, rebalance=rebalance)
    out = pd.DataFrame({"Strategy": res["equity"], "Strategy_Exposure": res["exposure"]})
    for b in benchmarks:
        t = pd.DataFrame(0.0, index=close_w.index, columns=close_w.columns)
        t[b] = 1.0
        out[b] = simulate(open_w, close_w, t, start)["equity"]
    return out.rename_axis("Date").reset_index()
