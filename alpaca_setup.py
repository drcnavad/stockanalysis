# alpaca_setup.py
"""Alpaca clients.

Importing this module creates ONLY a market-data client (`data_client`). No trading client is
created and no account endpoint is called at import time.

Trading access is explicit and defaults to PAPER:
    from alpaca_setup import get_trading_client
    paper_client = get_trading_client()            # paper=True by default
    live_client  = get_trading_client(paper=False) # only when you really mean live trading
"""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from alpaca.data.historical import StockHistoricalDataClient

# Load .env next to this file (works from notebooks, scripts and other working directories)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# Timezone
EASTERN = ZoneInfo("America/New_York")

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    raise EnvironmentError("Missing API keys. Make sure .env has ALPACA_API_KEY and ALPACA_SECRET_KEY")

# Market data only (historical bars / quotes / trades)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


def get_trading_client(paper=True):
    """Create a TradingClient on explicit request. Defaults to the PAPER environment."""
    from alpaca.trading.client import TradingClient
    return TradingClient(API_KEY, SECRET_KEY, paper=paper)


def get_account_summary(paper=True):
    """Account balances as a dict (explicit call only; defaults to PAPER)."""
    acct = get_trading_client(paper=paper).get_account()
    return {
        "buying_power": float(acct.non_marginable_buying_power),
        "cash": float(acct.cash),
        "margin_buying_power": float(acct.buying_power),
        "equity": float(acct.equity),
        "total_fees": float(acct.accrued_fees),
    }
