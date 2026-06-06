import os
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# Zerodha API credentials
KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")

# Instrument tokens
NIFTY_TOKEN = 256265
SENSEX_TOKEN = 265
NIFTY_BANK_TOKEN = 260105
INDIA_VIX_TOKEN = 264969

# Exchange constants
NSE = "NSE"
BSE = "BSE"
NFO = "NFO"
BFO = "BFO"

# Lot sizes
LOT_SIZES = {
    "NIFTY": 65,
    "SENSEX": 20,
    "BANKNIFTY": 15,
    "FINNIFTY": 40,
}

# Strike intervals (in points)
STRIKE_INTERVALS = {
    "NIFTY": 50,
    "SENSEX": 100,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
}

# Number of strikes to show on each side of ATM
STRIKES_AROUND_ATM = 15

# Risk-free rate (RBI repo rate approximation)
RISK_FREE_RATE = 0.065

# Historical data intervals
VALID_INTERVALS = ["minute", "3minute", "5minute", "10minute", "15minute", "30minute", "60minute", "day"]

# Trading hours
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30


def get_nearest_expiry(index: str = "NIFTY") -> date:
    """
    Returns the nearest upcoming expiry date.
    NIFTY: every Thursday
    SENSEX: every Friday (monthly), weekly on Tuesday and Thursday in some series
    For simplicity, NIFTY weekly = nearest Thursday, SENSEX weekly = nearest Friday.
    """
    today = date.today()
    if index.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
        # Weekly expiry: Thursday (weekday=3)
        days_ahead = (3 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return today + timedelta(days=days_ahead)
    else:
        # SENSEX weekly: Friday (weekday=4)
        days_ahead = (4 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return today + timedelta(days=days_ahead)


def get_monthly_expiry(index: str = "NIFTY") -> date:
    """
    Returns last Thursday (NIFTY) or last Friday (SENSEX) of the current month.
    """
    today = date.today()
    year = today.year
    month = today.month

    # Get last day of the month
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    target_weekday = 3 if index.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY") else 4
    # Walk backwards to find the last occurrence of target_weekday
    day = last_day
    while day.weekday() != target_weekday:
        day -= timedelta(days=1)

    # If monthly expiry has already passed, get next month
    if day < today:
        month = month + 1 if month < 12 else 1
        year = year + 1 if month == 1 else year
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)
        day = last_day
        while day.weekday() != target_weekday:
            day -= timedelta(days=1)

    return day


def get_expiry_list(index: str = "NIFTY", count: int = 5) -> list:
    """
    Returns list of next `count` expiry dates as strings (YYYY-MM-DD).
    """
    today = date.today()
    expiries = []
    target_weekday = 3 if index.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY") else 4

    check_date = today
    while len(expiries) < count:
        days_ahead = (target_weekday - check_date.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_expiry = check_date + timedelta(days=days_ahead)
        expiries.append(next_expiry.strftime("%Y-%m-%d"))
        check_date = next_expiry

    return expiries


def days_to_expiry(expiry_str: str) -> int:
    """Calculate calendar days to expiry from today."""
    expiry = date.fromisoformat(expiry_str)
    delta = expiry - date.today()
    return max(delta.days, 0)


def get_atm_strike(spot: float, index: str = "NIFTY") -> int:
    """Round spot price to nearest ATM strike."""
    interval = STRIKE_INTERVALS.get(index.upper(), 50)
    return int(round(spot / interval) * interval)


def get_strike_range(spot: float, index: str = "NIFTY", num_strikes: int = None) -> list:
    """
    Returns list of strikes around ATM for the given index.
    """
    if num_strikes is None:
        num_strikes = STRIKES_AROUND_ATM
    interval = STRIKE_INTERVALS.get(index.upper(), 50)
    atm = get_atm_strike(spot, index)
    strikes = []
    for i in range(-num_strikes, num_strikes + 1):
        strikes.append(atm + i * interval)
    return sorted(strikes)
