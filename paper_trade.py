"""Build (and optionally submit to Alpaca PAPER) the orders that move a portfolio to the strategy's target weights.

DEFAULT = DRY RUN: prints the order list and writes nothing to any broker.

    python paper_trade.py --account-size 25000                       # dry run, assumes an all-cash account
    python paper_trade.py --account-size 25000 --positions my.csv    # dry run vs current holdings (CSV: Symbol,Shares)
    python paper_trade.py --target provisional                       # use "if rebalanced at latest close" weights
    python paper_trade.py --target midweek --positions my.csv        # only the latest Mon/Wed mid-week swap(s) / exit(s)

Submitting requires BOTH flags and only ever talks to the PAPER environment:

    python paper_trade.py --submit --paper [--account-size N]

In submit mode the paper account's equity (unless --account-size is given) and positions are read from Alpaca,
sells are sent before buys as DAY market orders (whole shares). Live trading is not supported by this script.

Targets come from Reports/strategy_picks.csv (written by main_signal_analysis.ipynb):
  - `current`     = Strategy_Weight (portfolio decided at the last weekly rebalance)
  - `provisional` = Provisional_Weight (what the rules would pick at the latest close)
  - `midweek`     = the decisions of the latest Mon/Wed mid-week check (Reports/strategy_midweek_check.csv, rules C6-U96-MW30):
                    swap = SELL all shares of the stock that fell below rank 15 and BUY the new top-3 stock with the same dollars
                    (without --positions the dollars = the old stock's target weight x account size); exit = SELL all shares of
                    a stock ranked worse than 30, SELL-ONLY (the cash stays idle until the Friday rebalance). Nothing else is traded.
  - `auto` (default) = provisional on a rebalance day (the decision day itself); midweek when the latest bar is a Mon/Wed
                    check that produced a swap or an exit; otherwise current.
"""
import argparse
import math
import os
import sys

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PICKS_CSV = os.path.join(PROJECT_ROOT, "Reports", "strategy_picks.csv")
MIDWEEK_CSV = os.path.join(PROJECT_ROOT, "Reports", "strategy_midweek_check.csv")
SIGNAL_CSV = os.path.join(PROJECT_ROOT, "Reports", "signal_analysis.csv")
ORDER_COLUMNS = ["Symbol", "Side", "Shares", "Price", "Est_Value", "Current_Shares", "Target_Shares",
                 "Target_Weight_%", "Target_Value"]


# ----------------------------------------------------------------------------- target weights and prices (from the Reports CSVs)
def latest_midweek_swaps(midweek_csv=MIDWEEK_CSV, as_of=None):
    """Swap and exit rows (Action SWAP / SELL; Sell, Sell_Rank, Buy, Buy_Rank, Weight_%, Message, Event_Date,
    Applies_To_Open) of the latest mid-week check, only if that check was made at the latest bar (`as_of`). Exit rows
    (Action SELL) have no Buy. Empty DataFrame otherwise."""
    if not os.path.exists(midweek_csv):
        return pd.DataFrame()
    m = pd.read_csv(midweek_csv)
    if m.empty or "Event" not in m.columns:
        return pd.DataFrame()
    m = m[m["Event"] == "mid-week check"]
    if as_of is not None:
        m = m[m["Event_Date"].astype(str) == str(as_of)]
    return m[m["Action"].isin(["SWAP", "SELL"])].reset_index(drop=True)


def load_targets(source="auto", picks_csv=PICKS_CSV, midweek_csv=MIDWEEK_CSV):
    """(targets DataFrame[Symbol, Weight, Price], meta dict). Weights are fractions of the account (rest = cash).

    meta['swaps'] holds the latest mid-week swap rows (source 'midweek'); targets are then the weights after the swap."""
    picks = pd.read_csv(picks_csv)
    if picks.empty:
        raise ValueError(f"{picks_csv} is empty - run main_signal_analysis.ipynb first")
    as_of, last_reb = str(picks["As_Of"].iloc[0]), str(picks["Last_Rebalance"].iloc[0])
    last_dec = str(picks["Last_Decision"].iloc[0]) if "Last_Decision" in picks.columns else last_reb
    swaps = latest_midweek_swaps(midweek_csv, as_of)
    if source == "auto":
        source = "provisional" if as_of == last_reb else ("midweek" if len(swaps) else "current")
    if source == "midweek" and not len(swaps):
        raise ValueError(f"no mid-week swap or exit at the latest check ({as_of}) - nothing to trade (use target 'current')")
    col = {"current": "Strategy_Weight", "provisional": "Provisional_Weight", "midweek": "Strategy_Weight"}[source]
    t = picks.loc[picks[col].fillna(0) > 0, ["Symbol", col, "Close"]].rename(columns={col: "Weight", "Close": "Price"})
    return t.reset_index(drop=True), {"as_of": as_of, "last_rebalance": last_reb, "last_decision": last_dec, "source": source,
                                      "strategy": str(picks["Strategy"].iloc[0]), "invested": float(t["Weight"].sum()),
                                      "swaps": swaps}


def latest_prices(symbols, signal_csv=SIGNAL_CSV):
    """Latest close per symbol from signal_analysis.csv (used to price positions that are not in the targets)."""
    if not symbols:
        return {}
    df = pd.read_csv(signal_csv, usecols=["Date", "Symbol", "Close"], parse_dates=["Date"])
    df = df[df["Symbol"].isin(symbols)].sort_values("Date").drop_duplicates("Symbol", keep="last")
    return dict(zip(df["Symbol"], df["Close"]))


# ----------------------------------------------------------------------------- order sizing (whole shares by default)
def build_orders(targets, account_size, positions=None, prices=None, min_value=1.0, fractional=False):
    """Orders (sells first) to move `positions` {symbol: shares} to target weights of `account_size` dollars.

    Whole shares by default (rounded down, so the plan never needs more cash than `account_size`).
    Positions not in the targets are sold completely; trades worth less than `min_value` are skipped.
    """
    if not account_size or account_size <= 0 or not math.isfinite(account_size):
        raise ValueError("account_size must be a positive number")
    positions = {str(k).upper(): float(v) for k, v in (positions or {}).items() if float(v) != 0}
    px = dict(zip(targets["Symbol"], targets["Price"]))
    px.update({k: v for k, v in (prices or {}).items() if k not in px})
    rows = []
    wanted = dict(zip(targets["Symbol"], targets["Weight"]))
    for sym in sorted(set(wanted) | set(positions)):
        price = px.get(sym)
        cur = positions.get(sym, 0.0)
        weight = float(wanted.get(sym, 0.0))
        if price is None or not (price > 0):
            rows.append({"Symbol": sym, "Side": "SKIP (no price)", "Shares": 0, "Price": price, "Est_Value": 0.0,
                         "Current_Shares": cur, "Target_Shares": None, "Target_Weight_%": weight * 100, "Target_Value": weight * account_size})
            continue
        target_value = weight * account_size
        tgt = round(target_value / price, 4) if fractional else math.floor(target_value / price)
        delta = round(tgt - cur, 4)
        if delta == 0 or abs(delta) * price < min_value:
            side = "HOLD"
        else:
            side = "BUY" if delta > 0 else "SELL"
        rows.append({"Symbol": sym, "Side": side, "Shares": abs(delta) if side != "HOLD" else 0, "Price": float(price),
                     "Est_Value": round(abs(delta) * price, 2) if side != "HOLD" else 0.0, "Current_Shares": cur,
                     "Target_Shares": tgt, "Target_Weight_%": round(weight * 100, 2), "Target_Value": round(target_value, 2)})
    orders = pd.DataFrame(rows, columns=ORDER_COLUMNS)
    order = {"SELL": 0, "BUY": 1, "HOLD": 2}
    return orders.sort_values(["Side", "Symbol"], key=lambda s: s.map(order).fillna(3) if s.name == "Side" else s).reset_index(drop=True)


def build_swap_orders(swaps, account_size, positions=None, prices=None, fractional=False):
    """Orders for mid-week swaps: SELL every share of `Sell`, BUY `Buy` with the same dollars (shares x latest close).
    Exit rows (Action SELL, no Buy): SELL every share of `Sell` only - the cash stays idle until the weekly rebalance.

    If the account holds no `Sell` shares (no --positions given) the dollars = the swap's target weight x account_size.
    Whole shares (rounded down) by default. Every other holding is left alone (HOLD rows)."""
    if not account_size or account_size <= 0 or not math.isfinite(account_size):
        raise ValueError("account_size must be a positive number")
    positions = {str(k).upper(): float(v) for k, v in (positions or {}).items() if float(v) != 0}
    prices = prices or {}
    rows, touched = [], set()
    for r in swaps.itertuples():
        out_sym = str(r.Sell)
        in_sym = str(r.Buy) if isinstance(r.Buy, str) and r.Buy.strip() else None       # None = mid-week exit (sell only)
        p_out, p_in = prices.get(out_sym), prices.get(in_sym)
        have = positions.get(out_sym, 0.0)
        w = float(swaps.loc[r.Index, "Weight_%"]) / 100
        dollars = have * p_out if have and p_out else w * account_size
        rows.append({"Symbol": out_sym, "Side": "SELL" if have else "SELL (none held)", "Shares": have, "Price": p_out,
                     "Est_Value": round(have * p_out, 2) if have and p_out else 0.0, "Current_Shares": have, "Target_Shares": 0,
                     "Target_Weight_%": 0.0, "Target_Value": 0.0})
        if in_sym is None:
            touched.add(out_sym)
            continue
        if p_in and p_in > 0:
            q = round(dollars / p_in, 4) if fractional else math.floor(dollars / p_in)
            cur_in = positions.get(in_sym, 0.0)
            rows.append({"Symbol": in_sym, "Side": "BUY", "Shares": q, "Price": float(p_in), "Est_Value": round(q * p_in, 2),
                         "Current_Shares": cur_in, "Target_Shares": cur_in + q, "Target_Weight_%": round(w * 100, 2),
                         "Target_Value": round(dollars, 2)})
        else:
            rows.append({"Symbol": in_sym, "Side": "SKIP (no price)", "Shares": 0, "Price": p_in, "Est_Value": 0.0,
                         "Current_Shares": positions.get(in_sym, 0.0), "Target_Shares": None,
                         "Target_Weight_%": round(w * 100, 2), "Target_Value": round(dollars, 2)})
        touched |= {out_sym, in_sym}
    for sym in sorted(set(positions) - touched):
        px = prices.get(sym)
        rows.append({"Symbol": sym, "Side": "HOLD", "Shares": 0, "Price": px, "Est_Value": 0.0, "Current_Shares": positions[sym],
                     "Target_Shares": positions[sym], "Target_Weight_%": None,
                     "Target_Value": round(positions[sym] * px, 2) if px else None})
    orders = pd.DataFrame(rows, columns=ORDER_COLUMNS)
    order = {"SELL": 0, "SELL (none held)": 0, "BUY": 1, "HOLD": 2}
    return orders.sort_values(["Side", "Symbol"], key=lambda s: s.map(order).fillna(3) if s.name == "Side" else s).reset_index(drop=True)


def plan_orders(source, account_size, positions=None, picks_csv=PICKS_CSV, signal_csv=SIGNAL_CSV, midweek_csv=MIDWEEK_CSV,
                min_value=1.0, fractional=False):
    """(orders, meta, targets) for any target source; used by the CLI and the app's order preview. No broker calls."""
    targets, meta = load_targets(source, picks_csv, midweek_csv)
    positions = positions or {}
    if meta["source"] == "midweek":
        syms = sorted(set(meta["swaps"]["Sell"].astype(str)) | set(meta["swaps"]["Buy"].dropna().astype(str)) | set(positions))
        px = latest_prices(syms, signal_csv)
        px.update(dict(zip(targets["Symbol"], targets["Price"])))
        return build_swap_orders(meta["swaps"], account_size, positions, px, fractional=fractional), meta, targets
    prices = latest_prices([s for s in positions if s not in set(targets["Symbol"])], signal_csv)
    return build_orders(targets, account_size, positions, prices, min_value=min_value, fractional=fractional), meta, targets


# ----------------------------------------------------------------------------- positions file, PAPER submission (explicit --submit --paper only), command line
def read_positions_csv(path):
    """Positions CSV (Symbol,Shares or Symbol,Qty) -> {symbol: shares}."""
    pos = pd.read_csv(path)
    pos.columns = [c.strip().lower() for c in pos.columns]
    if "shares" not in pos.columns and "qty" not in pos.columns:
        raise SystemExit(f"{path}: needs a Shares column for order sizing (Symbol,Shares); a Weight-only file works for the alert only")
    qty = "shares" if "shares" in pos.columns else "qty"
    return dict(zip(pos["symbol"].astype(str).str.upper().str.strip(), pd.to_numeric(pos[qty], errors="coerce").fillna(0)))


def submit_paper(orders):
    """Send the BUY/SELL rows as DAY market orders to the Alpaca PAPER account (sells first)."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from alpaca_setup import get_trading_client
    client = get_trading_client(paper=True)
    results = []
    for r in orders[orders["Side"].isin(["SELL", "BUY"])].itertuples():
        req = MarketOrderRequest(symbol=r.Symbol, qty=r.Shares, time_in_force=TimeInForce.DAY,
                                 side=OrderSide.SELL if r.Side == "SELL" else OrderSide.BUY)
        try:
            o = client.submit_order(req)
            results.append((r.Symbol, r.Side, r.Shares, str(o.status)))
        except Exception as e:  # keep going; report every failure
            results.append((r.Symbol, r.Side, r.Shares, f"FAILED: {e}"))
    return pd.DataFrame(results, columns=["Symbol", "Side", "Shares", "Status"])


def main(argv=None):
    """Command line: print the order list (dry run) or submit to the Alpaca PAPER account."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--account-size", type=float, help="dollars to allocate (dry run default: 100000)")
    p.add_argument("--positions", help="CSV with Symbol,Shares of current holdings (dry run)")
    p.add_argument("--target", choices=["auto", "current", "provisional", "midweek"], default="auto")
    p.add_argument("--fractional", action="store_true", help="fractional share quantities (dry run preview only)")
    p.add_argument("--min-value", type=float, default=1.0, help="skip trades smaller than this many dollars")
    p.add_argument("--out", help="also write the order list to this CSV")
    p.add_argument("--submit", action="store_true", help="send the orders (requires --paper)")
    p.add_argument("--paper", action="store_true", help="confirm the PAPER environment for --submit")
    p.add_argument("--live", action="store_true", help=argparse.SUPPRESS)
    a = p.parse_args(argv)

    if a.live:
        sys.exit("Refusing: this script never trades a live account.")
    if a.submit and not a.paper:
        sys.exit("Refusing: --submit needs --paper as well (paper trading only).")
    if a.submit and a.fractional:
        sys.exit("Refusing: submit mode uses whole shares only.")

    positions = read_positions_csv(a.positions) if a.positions else {}
    account_size = a.account_size
    if a.submit:
        client_positions = __import__("alpaca_setup").get_trading_client(paper=True).get_all_positions()
        positions = {pos.symbol: float(pos.qty) for pos in client_positions}
        if account_size is None:
            account_size = __import__("alpaca_setup").get_account_summary(paper=True)["equity"]
    account_size = account_size or 100_000.0

    orders, meta, targets = plan_orders(a.target, account_size, positions, min_value=a.min_value, fractional=a.fractional)
    print(f"Strategy: {meta['strategy']} | targets = {meta['source']} weights | as of {meta['as_of']} "
          f"(last weekly rebalance {meta['last_rebalance']}, last decision {meta['last_decision']}) | invested {meta['invested']:.0%}")
    if meta["source"] == "midweek":
        for act, m in zip(meta["swaps"]["Action"], meta["swaps"]["Message"]):
            print(("MID-WEEK EXIT: " if act == "SELL" else "MID-WEEK SWAP: ") + m)
        if not positions and (meta["swaps"]["Action"] == "SWAP").any():
            print("(no --positions given: the buy is sized at the sold stock's target weight x account size)")
    print(f"Account size ${account_size:,.2f}; prices = latest close (actual fills will differ)\n")
    print(orders.to_string(index=False))
    buys, sells = orders.loc[orders.Side == "BUY", "Est_Value"].sum(), orders.loc[orders.Side == "SELL", "Est_Value"].sum()
    if meta["source"] == "midweek":
        print(f"\nBuys ${buys:,.2f} · Sells ${sells:,.2f} (swaps: same dollars, whole shares; exits: sell only - "
              "the cash stays idle until the Friday rebalance)")
    else:
        held_after = (orders["Target_Shares"].fillna(0) * orders["Price"].fillna(0)).sum()
        print(f"\nBuys ${buys:,.2f} · Sells ${sells:,.2f} · invested after ≈ ${held_after:,.2f} · cash after ≈ ${account_size - held_after:,.2f}")
    if a.out:
        orders.to_csv(a.out, index=False)
    if not a.submit:
        print("\nDRY RUN - nothing was sent. Use --submit --paper to send these orders to the Alpaca PAPER account.")
        return orders
    print("\nSubmitting to Alpaca PAPER ...")
    print(submit_paper(orders).to_string(index=False))
    return orders


if __name__ == "__main__":
    main()
