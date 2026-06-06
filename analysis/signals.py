"""
Master Signal Generator
Combines technical, options, and sentiment analysis to produce actionable trade signals.
"""
from typing import Dict, List, Any, Optional
from datetime import date

from config import get_nearest_expiry, get_atm_strike, LOT_SIZES, STRIKE_INTERVALS


def generate_trade_signals(
    index: str,
    spot: float,
    technical: Dict[str, Any],
    options: Dict[str, Any],
    sentiment: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Generate a comprehensive trade signal combining all analysis inputs.

    Parameters
    ----------
    index      : 'NIFTY' or 'SENSEX'
    spot       : Current spot price
    technical  : Output from generate_technical_signals()
    options    : Dict with pcr, max_pain, iv_rank, expected_move, strategies
    sentiment  : Output from get_market_sentiment()

    Returns
    -------
    {
        "action": "BUY" | "SELL" | "WAIT",
        "instrument": str,
        "entry_price": float,
        "target": float,
        "stop_loss": float,
        "risk_reward": float,
        "confidence": "HIGH" | "MEDIUM" | "LOW",
        "reasons": list[str],
        "expiry": str,
        "strategy": str,
        "lot_size": int,
        "signals_breakdown": dict
    }
    """
    index = index.upper()
    lot_size = LOT_SIZES.get(index, 25)
    strike_interval = STRIKE_INTERVALS.get(index, 50)
    atm = get_atm_strike(spot, index)
    atr = technical.get("atr", spot * 0.005)
    expiry = get_nearest_expiry(index).strftime("%Y-%m-%d")

    # Pull key signals
    tech_bias = technical.get("overall_bias", "NEUTRAL")
    tech_score = technical.get("signal_score", 0)
    sentiment_label = sentiment.get("label", "NEUTRAL")
    sentiment_score = sentiment.get("score", 0)
    pcr = options.get("pcr", {}).get("pcr_oi", 1.0)
    iv_rank = options.get("iv_rank", {}).get("iv_rank", 50)
    max_pain = options.get("max_pain", atm)
    expected_move = options.get("expected_move", {})
    oi_trend = options.get("oi_trend", {})

    reasons = list(technical.get("signals_list", []))

    # Composite direction score
    direction_score = tech_score  # -7 to +7 approx
    if sentiment_score > 2:
        direction_score += 1
        reasons.append(f"Market sentiment: {sentiment_label}")
    elif sentiment_score < -2:
        direction_score -= 1
        reasons.append(f"Market sentiment: {sentiment_label}")

    if pcr > 1.2:
        direction_score += 1
        reasons.append(f"PCR {pcr:.2f} — elevated put writing (bullish)")
    elif pcr < 0.8:
        direction_score -= 1
        reasons.append(f"PCR {pcr:.2f} — elevated call writing (bearish)")

    oi_bias = oi_trend.get("bias", "NEUTRAL")
    if oi_bias == "BULLISH":
        direction_score += 1
        reasons.append(f"OI trend: {oi_trend.get('interpretation', 'LONG_BUILDUP')}")
    elif oi_bias == "BEARISH":
        direction_score -= 1
        reasons.append(f"OI trend: {oi_trend.get('interpretation', 'SHORT_BUILDUP')}")

    # Max pain proximity (price tends to gravitate toward max pain near expiry)
    distance_from_max_pain = abs(spot - max_pain)
    if distance_from_max_pain < spot * 0.005:
        reasons.append(f"Spot near max pain ({max_pain}) — range-bound likely")

    # Determine action
    if direction_score >= 3:
        action = "BUY"
        instrument = f"{index} CE"
        entry_price = spot
        target = spot + 2 * atr
        stop_loss = spot - 1.5 * atr
        strategy = "MOMENTUM_BUY"
        option_target_strike = atm
    elif direction_score <= -3:
        action = "SELL"
        instrument = f"{index} PE"
        entry_price = spot
        target = spot - 2 * atr
        stop_loss = spot + 1.5 * atr
        strategy = "MOMENTUM_SELL"
        option_target_strike = atm
    else:
        action = "WAIT"
        instrument = f"{index} FUT"
        entry_price = spot
        target = spot
        stop_loss = spot
        strategy = "WAIT_FOR_CLARITY"
        option_target_strike = atm
        reasons.append("Mixed signals — wait for directional clarity")

    # If high IV, prefer selling strategies
    if iv_rank > 65 and action != "WAIT":
        if action == "BUY":
            strategy = "BULL_PUT_SPREAD"
            instrument = f"{index} Bull Put Spread"
            reasons.append(f"IV Rank {iv_rank:.0f}% — preferring credit strategy")
        else:
            strategy = "BEAR_CALL_SPREAD"
            instrument = f"{index} Bear Call Spread"
            reasons.append(f"IV Rank {iv_rank:.0f}% — preferring credit strategy")

    # R:R calculation
    risk = abs(entry_price - stop_loss)
    reward = abs(target - entry_price)
    rr = round(reward / risk, 2) if risk > 0 else 0

    # Confidence
    abs_score = abs(direction_score)
    if abs_score >= 5:
        confidence = "HIGH"
    elif abs_score >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "action": action,
        "instrument": instrument,
        "entry_price": round(entry_price, 2),
        "target": round(target, 2),
        "stop_loss": round(stop_loss, 2),
        "risk_reward": rr,
        "confidence": confidence,
        "reasons": reasons,
        "expiry": expiry,
        "strategy": strategy,
        "lot_size": lot_size,
        "atm_strike": atm,
        "direction_score": direction_score,
        "signals_breakdown": {
            "technical_score": tech_score,
            "technical_bias": tech_bias,
            "sentiment_score": round(sentiment_score, 2),
            "sentiment_label": sentiment_label,
            "pcr": round(pcr, 3),
            "iv_rank": round(iv_rank, 1),
            "max_pain": max_pain,
            "oi_bias": oi_bias,
        },
    }


def generate_futures_signal(
    index: str,
    spot: float,
    technical: Dict,
    atr_multiplier_target: float = 2.5,
    atr_multiplier_sl: float = 1.5,
) -> Dict[str, Any]:
    """
    Generate a simple futures trading signal based purely on technical analysis.
    Used when options data is not available.
    """
    atr = technical.get("atr", spot * 0.005)
    bias = technical.get("overall_bias", "NEUTRAL")
    trend = technical.get("trend", "SIDEWAYS")
    score = technical.get("signal_score", 0)
    lot_size = LOT_SIZES.get(index.upper(), 25)

    if bias == "BULLISH" and score >= 3:
        action = "BUY"
        entry = spot
        target = round(spot + atr * atr_multiplier_target, 2)
        sl = round(spot - atr * atr_multiplier_sl, 2)
    elif bias == "BEARISH" and score <= -3:
        action = "SELL"
        entry = spot
        target = round(spot - atr * atr_multiplier_target, 2)
        sl = round(spot + atr * atr_multiplier_sl, 2)
    else:
        action = "WAIT"
        entry = spot
        target = spot
        sl = spot

    risk = abs(entry - sl)
    reward = abs(target - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0

    return {
        "action": action,
        "instrument": f"{index.upper()} FUT",
        "entry_price": round(entry, 2),
        "target": round(target, 2),
        "stop_loss": round(sl, 2),
        "risk_reward": rr,
        "confidence": "MEDIUM" if action != "WAIT" else "LOW",
        "reasons": technical.get("signals_list", []),
        "expiry": get_nearest_expiry(index).strftime("%Y-%m-%d"),
        "strategy": f"{index.upper()}_FUTURES_{action}",
        "lot_size": lot_size,
        "direction_score": score,
    }


def format_signal_for_display(signal: Dict) -> Dict:
    """
    Format signal for frontend display with color coding and labels.
    """
    action = signal.get("action", "WAIT")
    confidence = signal.get("confidence", "LOW")

    color_map = {
        "BUY": "#00d964",
        "SELL": "#ff4757",
        "WAIT": "#ffd700",
    }
    confidence_color = {
        "HIGH": "#00d964",
        "MEDIUM": "#ffd700",
        "LOW": "#aaaaaa",
    }

    rr = signal.get("risk_reward", 0)
    entry = signal.get("entry_price", 0)
    target = signal.get("target", entry)
    sl = signal.get("stop_loss", entry)

    return {
        **signal,
        "action_color": color_map.get(action, "#ffffff"),
        "confidence_color": confidence_color.get(confidence, "#ffffff"),
        "risk_points": round(abs(entry - sl), 2),
        "reward_points": round(abs(target - entry), 2),
        "rr_label": f"1:{rr}" if rr > 0 else "N/A",
    }
