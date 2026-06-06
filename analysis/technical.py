"""
Technical Analysis Engine
Provides indicators, pivot points, support/resistance, and signal generation.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Any


# ---------------------------------------------------------------------------
# Individual Indicators
# ---------------------------------------------------------------------------

def calculate_sma(prices: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return prices.rolling(window=period, min_periods=1).mean()


def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return prices.ewm(span=period, adjust=False).mean()


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing method.
    Returns RSI series (0-100).
    """
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calculate_macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Dict[str, pd.Series]:
    """
    MACD (Moving Average Convergence Divergence).
    Returns dict with 'macd', 'signal', 'histogram' series.
    """
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram,
    }


def calculate_bollinger_bands(
    prices: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> Dict[str, pd.Series]:
    """
    Bollinger Bands.
    Returns dict with 'upper', 'middle', 'lower', 'bandwidth', 'pct_b'.
    """
    middle = calculate_sma(prices, period)
    std = prices.rolling(window=period, min_periods=1).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    bandwidth = (upper - lower) / middle * 100
    pct_b = (prices - lower) / (upper - lower).replace(0, np.nan)
    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "bandwidth": bandwidth,
        "pct_b": pct_b.fillna(0.5),
    }


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range.
    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calculate_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> Dict[str, pd.Series]:
    """
    Supertrend indicator.
    Returns dict with 'supertrend' (price level) and 'direction' (+1 bullish, -1 bearish).
    """
    atr = calculate_atr(high, low, close, period)
    hl2 = (high + low) / 2

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    supertrend = pd.Series(index=close.index, dtype=float)
    direction = pd.Series(index=close.index, dtype=int)

    for i in range(1, len(close)):
        idx = close.index[i]
        prev_idx = close.index[i - 1]

        # Upper band
        if basic_upper.iloc[i] < final_upper.iloc[i - 1] or close.iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        # Lower band
        if basic_lower.iloc[i] > final_lower.iloc[i - 1] or close.iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        # Direction
        if i == 1:
            direction.iloc[i] = 1
        elif supertrend.iloc[i - 1] == final_upper.iloc[i - 1]:
            direction.iloc[i] = -1 if close.iloc[i] > final_upper.iloc[i] else 1
        else:
            direction.iloc[i] = 1 if close.iloc[i] < final_lower.iloc[i] else -1

        supertrend.iloc[i] = final_lower.iloc[i] if direction.iloc[i] == -1 else final_upper.iloc[i]

    # Fill first row
    direction.iloc[0] = -1
    supertrend.iloc[0] = final_upper.iloc[0]

    return {
        "supertrend": supertrend,
        "direction": direction,
        "upper_band": final_upper,
        "lower_band": final_lower,
    }


def calculate_pivot_points(
    high: float,
    low: float,
    close: float,
) -> Dict[str, Dict[str, float]]:
    """
    Calculate Classic, Camarilla, and Fibonacci pivot points.
    Inputs are previous session's high, low, close.
    """
    pivot = (high + low + close) / 3

    # Classic pivots
    classic = {
        "pivot": round(pivot, 2),
        "r1": round(2 * pivot - low, 2),
        "r2": round(pivot + (high - low), 2),
        "r3": round(high + 2 * (pivot - low), 2),
        "s1": round(2 * pivot - high, 2),
        "s2": round(pivot - (high - low), 2),
        "s3": round(low - 2 * (high - pivot), 2),
    }

    # Camarilla pivots
    range_ = high - low
    camarilla = {
        "pivot": round(pivot, 2),
        "r1": round(close + range_ * 1.1 / 12, 2),
        "r2": round(close + range_ * 1.1 / 6, 2),
        "r3": round(close + range_ * 1.1 / 4, 2),
        "r4": round(close + range_ * 1.1 / 2, 2),
        "s1": round(close - range_ * 1.1 / 12, 2),
        "s2": round(close - range_ * 1.1 / 6, 2),
        "s3": round(close - range_ * 1.1 / 4, 2),
        "s4": round(close - range_ * 1.1 / 2, 2),
    }

    # Fibonacci pivots
    fibonacci = {
        "pivot": round(pivot, 2),
        "r1": round(pivot + 0.382 * range_, 2),
        "r2": round(pivot + 0.618 * range_, 2),
        "r3": round(pivot + 1.000 * range_, 2),
        "s1": round(pivot - 0.382 * range_, 2),
        "s2": round(pivot - 0.618 * range_, 2),
        "s3": round(pivot - 1.000 * range_, 2),
    }

    return {
        "classic": classic,
        "camarilla": camarilla,
        "fibonacci": fibonacci,
    }


def calculate_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """
    Volume Weighted Average Price (intraday, resets each day).
    """
    typical_price = (high + low + close) / 3
    tp_vol = typical_price * volume
    cumulative_tp_vol = tp_vol.cumsum()
    cumulative_vol = volume.cumsum()
    vwap = cumulative_tp_vol / cumulative_vol.replace(0, np.nan)
    return vwap.ffill()


def get_support_resistance(
    prices: pd.Series,
    window: int = 10,
    tolerance_pct: float = 0.003,
) -> Dict[str, List[float]]:
    """
    Identify key support and resistance levels using swing highs/lows.
    Merges nearby levels within tolerance_pct (0.3%) of each other.
    Returns dict with 'support' and 'resistance' lists (descending for support, ascending for resistance).
    """
    highs = []
    lows = []

    arr = prices.values
    for i in range(window, len(arr) - window):
        if arr[i] == max(arr[i - window: i + window + 1]):
            highs.append(float(arr[i]))
        if arr[i] == min(arr[i - window: i + window + 1]):
            lows.append(float(arr[i]))

    def merge_levels(levels: List[float]) -> List[float]:
        if not levels:
            return []
        levels = sorted(levels)
        merged = [levels[0]]
        for level in levels[1:]:
            if abs(level - merged[-1]) / merged[-1] <= tolerance_pct:
                merged[-1] = (merged[-1] + level) / 2  # average
            else:
                merged.append(level)
        return merged

    current_price = float(prices.iloc[-1])
    all_resistance = merge_levels([h for h in highs if h > current_price])
    all_support = merge_levels([l for l in lows if l < current_price])

    return {
        "resistance": sorted(all_resistance)[:5],
        "support": sorted(all_support, reverse=True)[:5],
    }


def calculate_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> Dict[str, pd.Series]:
    """Stochastic Oscillator (%K and %D)."""
    lowest_low = low.rolling(window=k_period, min_periods=1).min()
    highest_high = high.rolling(window=k_period, min_periods=1).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    k = k.fillna(50)
    d = k.rolling(window=d_period, min_periods=1).mean()
    return {"k": k, "d": d}


# ---------------------------------------------------------------------------
# Master Signal Generator
# ---------------------------------------------------------------------------

def generate_technical_signals(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Given an OHLCV DataFrame with columns [open, high, low, close, volume],
    compute all technical indicators and return a comprehensive signals dict.

    Returns:
      {
        "rsi": float,
        "rsi_signal": "OVERBOUGHT" | "OVERSOLD" | "NEUTRAL",
        "macd": {"macd": float, "signal": float, "histogram": float, "signal_type": str},
        "bb": {"upper": float, "middle": float, "lower": float, "pct_b": float, "position": str},
        "supertrend": {"direction": str, "level": float},
        "ema_20": float, "ema_50": float, "ema_200": float,
        "sma_20": float, "sma_50": float,
        "vwap": float,
        "atr": float,
        "stoch": {"k": float, "d": float, "signal": str},
        "pivots": {...},
        "support_resistance": {...},
        "trend": "UPTREND" | "DOWNTREND" | "SIDEWAYS",
        "overall_bias": "BULLISH" | "BEARISH" | "NEUTRAL",
        "signal_score": int,   # -5 to +5
        "signals_list": [list of active signal strings],
      }
    """
    if df.empty or len(df) < 20:
        return _empty_signals()

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df.get("volume", pd.Series(np.ones(len(df)), index=df.index))

    # RSI
    rsi_series = calculate_rsi(close, 14)
    rsi_val = round(float(rsi_series.iloc[-1]), 2)
    if rsi_val > 70:
        rsi_signal = "OVERBOUGHT"
    elif rsi_val < 30:
        rsi_signal = "OVERSOLD"
    else:
        rsi_signal = "NEUTRAL"

    # MACD
    macd_data = calculate_macd(close)
    macd_val = round(float(macd_data["macd"].iloc[-1]), 4)
    macd_sig = round(float(macd_data["signal"].iloc[-1]), 4)
    macd_hist = round(float(macd_data["histogram"].iloc[-1]), 4)
    prev_hist = float(macd_data["histogram"].iloc[-2]) if len(macd_data["histogram"]) > 1 else 0
    if macd_val > macd_sig and macd_hist > 0:
        macd_signal_type = "BULLISH"
    elif macd_val < macd_sig and macd_hist < 0:
        macd_signal_type = "BEARISH"
    elif macd_hist > 0 and prev_hist <= 0:
        macd_signal_type = "BULLISH_CROSSOVER"
    elif macd_hist < 0 and prev_hist >= 0:
        macd_signal_type = "BEARISH_CROSSOVER"
    else:
        macd_signal_type = "NEUTRAL"

    # Bollinger Bands
    bb_data = calculate_bollinger_bands(close, 20, 2.0)
    bb_upper = round(float(bb_data["upper"].iloc[-1]), 2)
    bb_mid = round(float(bb_data["middle"].iloc[-1]), 2)
    bb_lower = round(float(bb_data["lower"].iloc[-1]), 2)
    pct_b = round(float(bb_data["pct_b"].iloc[-1]), 3)
    if pct_b > 1.0:
        bb_position = "ABOVE_UPPER"
    elif pct_b > 0.8:
        bb_position = "NEAR_UPPER"
    elif pct_b < 0.0:
        bb_position = "BELOW_LOWER"
    elif pct_b < 0.2:
        bb_position = "NEAR_LOWER"
    else:
        bb_position = "MIDDLE"

    # Supertrend
    st_data = calculate_supertrend(high, low, close, 10, 3.0)
    st_dir = int(st_data["direction"].iloc[-1])
    st_level = round(float(st_data["supertrend"].iloc[-1]), 2)
    st_direction_str = "BULLISH" if st_dir == -1 else "BEARISH"

    # EMAs
    ema20 = round(float(calculate_ema(close, 20).iloc[-1]), 2)
    ema50 = round(float(calculate_ema(close, 50).iloc[-1]), 2)
    ema200 = round(float(calculate_ema(close, 200).iloc[-1]), 2) if len(close) >= 200 else None
    sma20 = round(float(calculate_sma(close, 20).iloc[-1]), 2)
    sma50 = round(float(calculate_sma(close, 50).iloc[-1]), 2)

    # VWAP
    vwap_val = round(float(calculate_vwap(high, low, close, volume).iloc[-1]), 2)

    # ATR
    atr_val = round(float(calculate_atr(high, low, close, 14).iloc[-1]), 2)

    # Stochastic
    stoch_data = calculate_stochastic(high, low, close)
    stoch_k = round(float(stoch_data["k"].iloc[-1]), 2)
    stoch_d = round(float(stoch_data["d"].iloc[-1]), 2)
    if stoch_k > 80:
        stoch_signal = "OVERBOUGHT"
    elif stoch_k < 20:
        stoch_signal = "OVERSOLD"
    elif stoch_k > stoch_d:
        stoch_signal = "BULLISH"
    else:
        stoch_signal = "BEARISH"

    # Pivots (using last completed candle OHLC)
    prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    pivots = calculate_pivot_points(
        float(prev["high"]), float(prev["low"]), float(prev["close"])
    )

    # Support / Resistance
    sr = get_support_resistance(close, window=5)

    # Current price
    current_price = float(close.iloc[-1])

    # Trend determination (EMA alignment)
    if ema20 > ema50 and (ema200 is None or ema50 > ema200) and current_price > ema20:
        trend = "UPTREND"
    elif ema20 < ema50 and (ema200 is None or ema50 < ema200) and current_price < ema20:
        trend = "DOWNTREND"
    else:
        trend = "SIDEWAYS"

    # Score-based overall bias
    score = 0
    signals_list = []

    if rsi_signal == "OVERSOLD":
        score += 1
        signals_list.append("RSI oversold (bullish)")
    elif rsi_signal == "OVERBOUGHT":
        score -= 1
        signals_list.append("RSI overbought (bearish)")

    if macd_signal_type in ("BULLISH", "BULLISH_CROSSOVER"):
        score += 1
        signals_list.append("MACD bullish")
    elif macd_signal_type in ("BEARISH", "BEARISH_CROSSOVER"):
        score -= 1
        signals_list.append("MACD bearish")

    if st_direction_str == "BULLISH":
        score += 1
        signals_list.append("Supertrend bullish")
    else:
        score -= 1
        signals_list.append("Supertrend bearish")

    if trend == "UPTREND":
        score += 1
        signals_list.append("EMA uptrend alignment")
    elif trend == "DOWNTREND":
        score -= 1
        signals_list.append("EMA downtrend alignment")

    if current_price > vwap_val:
        score += 1
        signals_list.append("Price above VWAP (bullish)")
    else:
        score -= 1
        signals_list.append("Price below VWAP (bearish)")

    if stoch_signal == "OVERSOLD":
        score += 1
        signals_list.append("Stochastic oversold (bullish)")
    elif stoch_signal == "OVERBOUGHT":
        score -= 1
        signals_list.append("Stochastic overbought (bearish)")

    if bb_position in ("BELOW_LOWER", "NEAR_LOWER"):
        score += 1
        signals_list.append("Price near lower Bollinger Band (potential reversal)")
    elif bb_position in ("ABOVE_UPPER", "NEAR_UPPER"):
        score -= 1
        signals_list.append("Price near upper Bollinger Band (caution)")

    if score >= 3:
        overall_bias = "BULLISH"
    elif score <= -3:
        overall_bias = "BEARISH"
    else:
        overall_bias = "NEUTRAL"

    return {
        "rsi": rsi_val,
        "rsi_signal": rsi_signal,
        "macd": {
            "macd": macd_val,
            "signal": macd_sig,
            "histogram": macd_hist,
            "signal_type": macd_signal_type,
        },
        "bb": {
            "upper": bb_upper,
            "middle": bb_mid,
            "lower": bb_lower,
            "pct_b": pct_b,
            "position": bb_position,
        },
        "supertrend": {
            "direction": st_direction_str,
            "level": st_level,
        },
        "ema_20": ema20,
        "ema_50": ema50,
        "ema_200": ema200,
        "sma_20": sma20,
        "sma_50": sma50,
        "vwap": vwap_val,
        "atr": atr_val,
        "stoch": {
            "k": stoch_k,
            "d": stoch_d,
            "signal": stoch_signal,
        },
        "pivots": pivots,
        "support_resistance": sr,
        "trend": trend,
        "overall_bias": overall_bias,
        "signal_score": score,
        "signals_list": signals_list,
        "current_price": current_price,
    }


def _empty_signals() -> Dict[str, Any]:
    """Return empty/default signals dict when insufficient data."""
    return {
        "rsi": 50.0,
        "rsi_signal": "NEUTRAL",
        "macd": {"macd": 0, "signal": 0, "histogram": 0, "signal_type": "NEUTRAL"},
        "bb": {"upper": 0, "middle": 0, "lower": 0, "pct_b": 0.5, "position": "MIDDLE"},
        "supertrend": {"direction": "NEUTRAL", "level": 0},
        "ema_20": 0, "ema_50": 0, "ema_200": None,
        "sma_20": 0, "sma_50": 0,
        "vwap": 0, "atr": 0,
        "stoch": {"k": 50, "d": 50, "signal": "NEUTRAL"},
        "pivots": {
            "classic": {"pivot": 0, "r1": 0, "r2": 0, "r3": 0, "s1": 0, "s2": 0, "s3": 0},
            "camarilla": {},
            "fibonacci": {},
        },
        "support_resistance": {"resistance": [], "support": []},
        "trend": "SIDEWAYS",
        "overall_bias": "NEUTRAL",
        "signal_score": 0,
        "signals_list": ["Insufficient data for analysis"],
        "current_price": 0,
    }
