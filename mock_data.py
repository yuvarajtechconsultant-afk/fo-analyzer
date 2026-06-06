"""
Realistic Mock Data Generator — 1 Month of NIFTY & SENSEX F&O Data
Generates OHLCV candles, option chains, FII/DII data, and market breadth
that closely mimics real Indian market behavior.
"""
import random
import math
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional

# ── Seed for reproducibility within a session ─────────────────────────────────
random.seed(42)

# ── Market constants ───────────────────────────────────────────────────────────
NIFTY_BASE   = 22400.0
SENSEX_BASE  = 73800.0
VIX_BASE     = 13.8

MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 30)

# Realistic daily volatility (annualised ~15%)
DAILY_VOL = 0.0085
INTRADAY_VOL = 0.0035


# ── Date helpers ───────────────────────────────────────────────────────────────

def _trading_days(n: int = 22) -> List[date]:
    """Return last n trading days (Mon–Fri, excluding simple weekends)."""
    days = []
    d = date.today() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:          # Mon=0 … Fri=4
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _intraday_timestamps(day: date, interval_min: int = 5) -> List[datetime]:
    """Return intraday timestamps for a trading day."""
    ts = []
    t = datetime(day.year, day.month, day.day, *MARKET_OPEN)
    close = datetime(day.year, day.month, day.day, *MARKET_CLOSE)
    while t <= close:
        ts.append(t)
        t += timedelta(minutes=interval_min)
    return ts


# ── Core price simulator ───────────────────────────────────────────────────────

def _simulate_prices(base: float, n_days: int = 22,
                     trend: float = 0.0003) -> List[float]:
    """
    Simulate daily closing prices using Geometric Brownian Motion
    with a slight upward drift and occasional gap days.
    """
    prices = [base]
    for _ in range(n_days - 1):
        shock   = random.gauss(0, DAILY_VOL)
        # Occasional large move (earnings / events) ~5% of days
        if random.random() < 0.05:
            shock += random.choice([-1, 1]) * random.uniform(0.015, 0.03)
        ret     = trend + shock
        prices.append(round(prices[-1] * (1 + ret), 2))
    return prices


def _make_candle(open_: float, vol: float, sentiment: float = 0.0) -> Dict:
    """Build a single OHLCV candle around an open price."""
    body   = open_ * vol * random.gauss(sentiment, 1.0)
    close  = round(open_ + body, 2)
    wick_u = abs(body) * random.uniform(0.1, 0.6)
    wick_d = abs(body) * random.uniform(0.1, 0.6)
    high   = round(max(open_, close) + wick_u, 2)
    low    = round(min(open_, close) - wick_d, 2)
    volume = random.randint(80_000, 600_000)
    return {"open": open_, "high": high, "low": low, "close": close,
            "volume": volume}


# ── Public API ─────────────────────────────────────────────────────────────────

def get_mock_historical(index: str, interval: str = "5minute",
                        days: int = 30) -> List[Dict]:
    """
    Return realistic OHLCV candles for the requested index and interval.
    Always covers at least `days` calendar days of data.
    """
    index = index.upper()
    base  = NIFTY_BASE if index == "NIFTY" else SENSEX_BASE
    trend = 0.0002 if index == "NIFTY" else 0.00018

    n_trading = min(days, 22)          # ≤22 trading days in a month
    daily_closes = _simulate_prices(base, n_trading, trend)

    interval_map = {
        "minute":   1,  "3minute":  3,  "5minute":  5,
        "10minute": 10, "15minute": 15, "30minute": 30,
        "60minute": 60, "day":      0,
    }
    interval_min = interval_map.get(interval, 5)
    trading_days = _trading_days(n_trading)

    candles = []

    if interval_min == 0:
        # Daily candles
        prev_close = base
        for i, day in enumerate(trading_days):
            close = daily_closes[i]
            gap   = prev_close * random.uniform(-0.004, 0.004)
            open_ = round(prev_close + gap, 2)
            c     = _make_candle(open_, DAILY_VOL * 1.5,
                                 sentiment=1.0 if close > open_ else -1.0)
            c["close"] = close
            c["high"]  = max(c["high"], close, open_)
            c["low"]   = min(c["low"],  close, open_)
            candles.append({
                "date":   datetime(day.year, day.month, day.day, 15, 30).isoformat(),
                "open":   open_, "high": c["high"],
                "low":    c["low"], "close": close,
                "volume": c["volume"], "oi": random.randint(10_000_000, 20_000_000),
            })
            prev_close = close
    else:
        # Intraday candles
        for i, day in enumerate(trading_days):
            day_close = daily_closes[i]
            day_open  = daily_closes[i - 1] if i > 0 else base
            day_open  = round(day_open * (1 + random.uniform(-0.003, 0.003)), 2)

            timestamps = _intraday_timestamps(day, interval_min)
            n_bars     = len(timestamps)

            # Build an intraday price path that reaches day_close
            path = [day_open]
            for j in range(1, n_bars):
                progress  = j / n_bars
                pull      = (day_close - path[-1]) * progress * 0.15
                shock     = path[-1] * INTRADAY_VOL * random.gauss(0, 1)
                # Morning volatility boost
                if j < 6:
                    shock *= 1.8
                path.append(round(path[-1] + pull + shock, 2))
            path[-1] = day_close     # ensure last bar closes at day_close

            day_oi = random.randint(8_000_000, 18_000_000)
            for j, ts in enumerate(timestamps):
                open_  = path[j]
                close  = path[j]
                # Small wicks
                hi = round(max(open_, close) + abs(open_ - close) * random.uniform(0.05, 0.4) + open_ * 0.0005, 2)
                lo = round(min(open_, close) - abs(open_ - close) * random.uniform(0.05, 0.4) - open_ * 0.0005, 2)
                vol = int(random.triangular(50_000, 400_000, 120_000))
                candles.append({
                    "date":   ts.isoformat(),
                    "open":   open_, "high": hi,
                    "low":    lo,    "close": close,
                    "volume": vol,   "oi":    day_oi,
                })

    return candles


def get_mock_quote(index: str) -> Dict:
    """Return a realistic spot quote with OHLC."""
    index = index.upper()
    if index == "VIX":
        vix = round(VIX_BASE + random.gauss(0, 0.4), 2)
        chg = round(random.gauss(0, 0.3), 2)
        return {
            "symbol": "VIX", "last_price": vix,
            "change": chg, "change_pct": round(chg / VIX_BASE * 100, 2),
            "open": round(vix - abs(chg), 2),
            "high": round(vix + 0.5, 2), "low": round(vix - 0.5, 2),
            "close": round(vix - chg, 2), "volume": 0, "oi": 0,
            "timestamp": datetime.now().isoformat(),
        }

    base  = NIFTY_BASE if index == "NIFTY" else SENSEX_BASE
    chg   = round(base * random.gauss(0, DAILY_VOL * 0.6), 2)
    price = round(base + chg, 2)
    return {
        "symbol": index, "last_price": price,
        "change": chg, "change_pct": round(chg / base * 100, 2),
        "open":  round(base + base * random.uniform(-0.003, 0.003), 2),
        "high":  round(price + abs(chg) * random.uniform(0.3, 0.8), 2),
        "low":   round(price - abs(chg) * random.uniform(0.3, 0.8), 2),
        "close": base,
        "volume": random.randint(500_000, 2_000_000),
        "oi":    random.randint(10_000_000, 20_000_000),
        "timestamp": datetime.now().isoformat(),
    }


def get_mock_option_chain(index: str, expiry: str,
                          spot: Optional[float] = None) -> Dict:
    """
    Generate a realistic option chain with proper OI skew,
    IV smile, and realistic premiums using intrinsic + time value.
    """
    index = index.upper()
    if spot is None:
        spot = NIFTY_BASE if index == "NIFTY" else SENSEX_BASE

    interval = 50 if index == "NIFTY" else 100
    n_strikes = 15      # each side of ATM

    atm = int(round(spot / interval) * interval)
    strikes = [atm + i * interval for i in range(-n_strikes, n_strikes + 1)]

    # Days to expiry
    try:
        exp_date = date.fromisoformat(expiry)
        dte = max((exp_date - date.today()).days, 1)
    except Exception:
        dte = 7
    T = dte / 365.0

    # Base ATM IV — realistic for Indian markets
    atm_iv = random.uniform(0.12, 0.18)

    # OI distribution — peak at ATM ± 2 strikes, heavier on PE side (skew)
    def oi_weight(strike: int, opt_type: str) -> float:
        dist  = abs(strike - atm) / interval
        skew  = 1.2 if opt_type == "PE" else 1.0     # PE skew
        decay = math.exp(-0.18 * dist)
        return max(0.05, decay * skew * random.uniform(0.7, 1.3))

    # Build chain
    chain = []
    total_ce_oi = 0
    total_pe_oi = 0

    for strike in strikes:
        moneyness = (spot - strike) / spot

        # IV smile — higher IV for deep OTM/ITM
        smile_factor = 1.0 + 2.5 * (moneyness ** 2)
        ce_iv = atm_iv * smile_factor * random.uniform(0.95, 1.05)
        pe_iv = atm_iv * smile_factor * random.uniform(0.95, 1.05) * 1.05  # PE premium

        # Black-Scholes approximate premium
        def _bs_approx(S, K, T, iv, opt):
            if T <= 0:
                return max(S - K, 0) if opt == "CE" else max(K - S, 0)
            d = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
            if opt == "CE":
                intrinsic = max(S - K, 0)
                time_val  = S * iv * math.sqrt(T / (2 * math.pi)) * math.exp(-0.5 * d * d)
            else:
                intrinsic = max(K - S, 0)
                time_val  = S * iv * math.sqrt(T / (2 * math.pi)) * math.exp(-0.5 * d * d)
            return round(max(0.05, intrinsic + time_val * random.uniform(0.85, 1.15)), 2)

        ce_ltp = _bs_approx(spot, strike, T, ce_iv, "CE")
        pe_ltp = _bs_approx(spot, strike, T, pe_iv, "PE")

        # OI — in lots (lot size 25 for NIFTY, 10 for SENSEX)
        lot = 25 if index == "NIFTY" else 10
        ce_oi_lots = int(oi_weight(strike, "CE") * random.randint(5000, 80000))
        pe_oi_lots = int(oi_weight(strike, "PE") * random.randint(5000, 80000))
        ce_oi = ce_oi_lots * lot
        pe_oi = pe_oi_lots * lot

        # OI change (today vs yesterday)
        ce_oi_chg = int(ce_oi * random.uniform(-0.15, 0.25))
        pe_oi_chg = int(pe_oi * random.uniform(-0.15, 0.25))

        ce_vol = int(ce_oi * random.uniform(0.05, 0.3))
        pe_vol = int(pe_oi * random.uniform(0.05, 0.3))

        total_ce_oi += ce_oi
        total_pe_oi += pe_oi

        chain.append({
            "strike": strike,
            "CE": {
                "ltp":        ce_ltp,
                "oi":         ce_oi,
                "oi_change":  ce_oi_chg,
                "volume":     ce_vol,
                "iv":         round(ce_iv * 100, 2),
                "greeks":     {},
            },
            "PE": {
                "ltp":        pe_ltp,
                "oi":         pe_oi,
                "oi_change":  pe_oi_chg,
                "volume":     pe_vol,
                "iv":         round(pe_iv * 100, 2),
                "greeks":     {},
            },
        })

    return {
        "index":  index,
        "spot":   spot,
        "expiry": expiry,
        "chain":  chain,
        "meta": {
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "pcr_oi":      round(total_pe_oi / max(total_ce_oi, 1), 3),
            "atm_iv":      round(atm_iv * 100, 2),
            "dte":         dte,
        },
    }


def get_mock_option_chain_history(index: str, expiry: str,
                                   spot_now: Optional[float] = None,
                                   days: int = 22) -> List[Dict]:
    """
    Generate daily historical option chain snapshots for the past `days` trading days.
    Each snapshot includes: date, spot, ATM, PCR, max pain, IV, top OI strikes.
    Simulates how OI builds up as expiry approaches.
    """
    index  = index.upper()
    base   = spot_now or (NIFTY_BASE if index == "NIFTY" else SENSEX_BASE)
    interval = 50 if index == "NIFTY" else 100
    lot      = 25 if index == "NIFTY" else 10

    try:
        exp_date  = date.fromisoformat(expiry)
    except Exception:
        exp_date  = date.today() + timedelta(days=7)

    trading_days_list = _trading_days(days)
    spot_series       = _simulate_prices(base, days, trend=0.0002)

    # VIX-like IV decreases as we approach expiry (term structure)
    base_iv = random.uniform(0.13, 0.19)

    snapshots = []
    for i, day in enumerate(trading_days_list):
        spot      = spot_series[i]
        dte_today = max((exp_date - day).days, 0)
        T         = max(dte_today / 365.0, 0.001)

        # IV rises as expiry nears (event risk) + random walk
        iv_factor = 1.0 + max(0, (7 - dte_today) / 30) * 0.3
        atm_iv    = base_iv * iv_factor * random.uniform(0.92, 1.08)

        atm = int(round(spot / interval) * interval)
        n   = 10   # strikes each side
        strikes = [atm + j * interval for j in range(-n, n + 1)]

        total_ce_oi = 0
        total_pe_oi = 0
        ce_oi_by_strike: Dict[int, int] = {}
        pe_oi_by_strike: Dict[int, int] = {}
        max_pain_pain: Dict[int, float] = {}

        # OI builds up as days progress — more OI near expiry
        oi_scale = 0.3 + 0.7 * (i / max(days - 1, 1))

        for s in strikes:
            dist  = abs(s - atm) / interval
            decay = math.exp(-0.2 * dist)

            # PE heavier OI on lower strikes (put wall), CE heavier on upper (call wall)
            pe_bias = 1.3 if s <= atm else 0.8
            ce_bias = 1.3 if s >= atm else 0.8

            ce_oi = int(decay * ce_bias * oi_scale * random.randint(100_000, 2_000_000) * lot)
            pe_oi = int(decay * pe_bias * oi_scale * random.randint(100_000, 2_000_000) * lot)

            ce_oi_by_strike[s] = ce_oi
            pe_oi_by_strike[s] = pe_oi
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi

            # Max pain: sum of OI loss at each strike
            pain = 0.0
            for k, c_oi in ce_oi_by_strike.items():
                pain += max(0, s - k) * c_oi
            for k, p_oi in pe_oi_by_strike.items():
                pain += max(0, k - s) * p_oi
            max_pain_pain[s] = pain

        max_pain_strike = min(max_pain_pain, key=max_pain_pain.get)
        pcr = round(total_pe_oi / max(total_ce_oi, 1), 3)

        # Top 5 CE strikes by OI
        top_ce = sorted(ce_oi_by_strike.items(), key=lambda x: -x[1])[:5]
        top_pe = sorted(pe_oi_by_strike.items(), key=lambda x: -x[1])[:5]

        # Build compact chain (ATM ± 5 strikes only for history)
        chain_slice = []
        for s in strikes:
            if abs(s - atm) <= 5 * interval:
                # OI change vs previous day (simulated)
                ce_chg = int(ce_oi_by_strike[s] * random.uniform(-0.1, 0.2))
                pe_chg = int(pe_oi_by_strike[s] * random.uniform(-0.1, 0.2))

                # Black-Scholes approx LTP
                def _ltp(S, K, T_, iv_, opt):
                    if T_ <= 0:
                        return max(S - K, 0) if opt == "CE" else max(K - S, 0)
                    intrinsic = max(S - K, 0) if opt == "CE" else max(K - S, 0)
                    tv = S * iv_ * math.sqrt(T_ / (2 * math.pi))
                    return round(max(0.05, intrinsic + tv * random.uniform(0.85, 1.1)), 2)

                smile = 1.0 + 2.0 * ((s - atm) / atm) ** 2
                ce_iv = atm_iv * smile
                pe_iv = atm_iv * smile * 1.05

                chain_slice.append({
                    "strike":    s,
                    "CE": {
                        "ltp":       _ltp(spot, s, T, ce_iv, "CE"),
                        "oi":        ce_oi_by_strike[s],
                        "oi_change": ce_chg,
                        "iv":        round(ce_iv * 100, 2),
                    },
                    "PE": {
                        "ltp":       _ltp(spot, s, T, pe_iv, "PE"),
                        "oi":        pe_oi_by_strike[s],
                        "oi_change": pe_chg,
                        "iv":        round(pe_iv * 100, 2),
                    },
                })

        snapshots.append({
            "date":         day.strftime("%Y-%m-%d"),
            "display_date": day.strftime("%d %b"),
            "spot":         round(spot, 2),
            "atm":          atm,
            "dte":          dte_today,
            "atm_iv":       round(atm_iv * 100, 2),
            "pcr_oi":       pcr,
            "pcr_sentiment": "BEARISH" if pcr < 0.7 else ("BULLISH" if pcr > 1.2 else "NEUTRAL"),
            "max_pain":     max_pain_strike,
            "total_ce_oi":  total_ce_oi,
            "total_pe_oi":  total_pe_oi,
            "top_ce_strikes": [{"strike": s, "oi": o} for s, o in top_ce],
            "top_pe_strikes": [{"strike": s, "oi": o} for s, o in top_pe],
            "chain":        chain_slice,
        })

    return snapshots


def get_mock_fii_dii() -> Dict:
    """Return 22 days of realistic FII/DII buy-sell activity."""
    days = _trading_days(22)
    rows = []
    for d in days:
        fii_buy  = round(random.uniform(3000, 12000), 2)
        fii_sell = round(random.uniform(3000, 12000), 2)
        dii_buy  = round(random.uniform(2000,  8000), 2)
        dii_sell = round(random.uniform(2000,  8000), 2)
        rows.append({
            "date":      d.strftime("%d-%b-%Y"),
            "fii_buy":   fii_buy,
            "fii_sell":  fii_sell,
            "fii_net":   round(fii_buy - fii_sell, 2),
            "dii_buy":   dii_buy,
            "dii_sell":  dii_sell,
            "dii_net":   round(dii_buy - dii_sell, 2),
        })
    # Latest day summary
    latest = rows[-1]
    return {
        "history":  rows,
        "fii_net":  latest["fii_net"],
        "dii_net":  latest["dii_net"],
        "fii_buy":  latest["fii_buy"],
        "fii_sell": latest["fii_sell"],
        "dii_buy":  latest["dii_buy"],
        "dii_sell": latest["dii_sell"],
    }


def get_mock_market_breadth() -> Dict:
    """Return advance/decline and other breadth indicators."""
    advances = random.randint(900, 1600)
    declines = random.randint(400, 1200)
    unchanged = 2000 - advances - declines
    return {
        "advances":    advances,
        "declines":    declines,
        "unchanged":   max(0, unchanged),
        "ad_ratio":    round(advances / max(declines, 1), 2),
        "new_highs":   random.randint(20, 120),
        "new_lows":    random.randint(5,  60),
        "above_200dma": random.randint(55, 75),   # % of stocks above 200 DMA
    }
