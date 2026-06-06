"""
Market Breadth and Sentiment Analysis
FII/DII activity, advance/decline ratio, composite sentiment scoring.
"""
from typing import Dict, List, Any, Optional


# ---------------------------------------------------------------------------
# FII / DII Activity
# ---------------------------------------------------------------------------

def get_fii_dii_activity(data: List[Dict]) -> Dict[str, Any]:
    """
    Parse and summarize FII/DII net buy/sell data.

    Expected input format (list of daily records):
    [
      {
        "date": "2024-01-15",
        "fii_buy": 12000.5,      # crore
        "fii_sell": 9800.2,
        "dii_buy": 7500.0,
        "dii_sell": 6200.3,
      },
      ...
    ]

    Returns aggregated stats and trend analysis.
    """
    if not data:
        return _empty_fii_dii()

    total_fii_buy = sum(r.get("fii_buy", 0) for r in data)
    total_fii_sell = sum(r.get("fii_sell", 0) for r in data)
    total_dii_buy = sum(r.get("dii_buy", 0) for r in data)
    total_dii_sell = sum(r.get("dii_sell", 0) for r in data)

    fii_net = round(total_fii_buy - total_fii_sell, 2)
    dii_net = round(total_dii_buy - total_dii_sell, 2)

    # Calculate 5-day rolling net for trend
    recent = data[-5:] if len(data) >= 5 else data
    fii_5d_net = sum(r.get("fii_buy", 0) - r.get("fii_sell", 0) for r in recent)
    dii_5d_net = sum(r.get("dii_buy", 0) - r.get("dii_sell", 0) for r in recent)

    # Determine sentiment contribution
    if fii_net > 500:
        fii_sentiment = "STRONG_BUYING"
    elif fii_net > 0:
        fii_sentiment = "BUYING"
    elif fii_net > -500:
        fii_sentiment = "SELLING"
    else:
        fii_sentiment = "STRONG_SELLING"

    if dii_net > 0:
        dii_sentiment = "BUYING"
    else:
        dii_sentiment = "SELLING"

    # Latest day
    latest = data[-1] if data else {}
    latest_fii_net = latest.get("fii_buy", 0) - latest.get("fii_sell", 0)
    latest_dii_net = latest.get("dii_buy", 0) - latest.get("dii_sell", 0)

    return {
        "fii_net_total": fii_net,
        "dii_net_total": dii_net,
        "fii_net_5d": round(fii_5d_net, 2),
        "dii_net_5d": round(dii_5d_net, 2),
        "fii_sentiment": fii_sentiment,
        "dii_sentiment": dii_sentiment,
        "latest_date": latest.get("date", ""),
        "latest_fii_net": round(latest_fii_net, 2),
        "latest_dii_net": round(latest_dii_net, 2),
        "combined_net": round(fii_net + dii_net, 2),
        "records": len(data),
    }


def _empty_fii_dii() -> Dict:
    return {
        "fii_net_total": 0,
        "dii_net_total": 0,
        "fii_net_5d": 0,
        "dii_net_5d": 0,
        "fii_sentiment": "NEUTRAL",
        "dii_sentiment": "NEUTRAL",
        "latest_date": "",
        "latest_fii_net": 0,
        "latest_dii_net": 0,
        "combined_net": 0,
        "records": 0,
    }


# ---------------------------------------------------------------------------
# Advance / Decline
# ---------------------------------------------------------------------------

def calculate_advance_decline(
    advances: int,
    declines: int,
    unchanged: int = 0,
) -> Dict[str, Any]:
    """
    Calculate advance-decline ratio and breadth signals.

    Returns ratio, net advances, and breadth interpretation.
    """
    total = advances + declines + unchanged
    if total == 0:
        return {
            "ratio": 1.0,
            "net": 0,
            "advances": 0,
            "declines": 0,
            "unchanged": 0,
            "total": 0,
            "breadth": "NEUTRAL",
            "pct_advancing": 0.0,
        }

    ratio = round(advances / declines, 3) if declines > 0 else float(advances)
    net = advances - declines
    pct_advancing = round(advances / total * 100, 2)

    if ratio > 2.0:
        breadth = "STRONGLY_POSITIVE"
    elif ratio > 1.5:
        breadth = "POSITIVE"
    elif ratio > 1.0:
        breadth = "SLIGHTLY_POSITIVE"
    elif ratio == 1.0:
        breadth = "NEUTRAL"
    elif ratio > 0.67:
        breadth = "SLIGHTLY_NEGATIVE"
    elif ratio > 0.5:
        breadth = "NEGATIVE"
    else:
        breadth = "STRONGLY_NEGATIVE"

    return {
        "ratio": ratio,
        "net": net,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "total": total,
        "breadth": breadth,
        "pct_advancing": pct_advancing,
    }


# ---------------------------------------------------------------------------
# Composite Market Sentiment
# ---------------------------------------------------------------------------

def get_market_sentiment(
    pcr: float,
    vix: float,
    advance_decline_ratio: float,
    fii_net: float,
    trend: str = "NEUTRAL",
) -> Dict[str, Any]:
    """
    Compute a composite market sentiment score from multiple inputs.

    Scoring (each component contributes -2 to +2):
      PCR: >1.2 bullish, <0.8 bearish
      VIX: <13 low fear (bullish), >20 high fear (bearish)
      A/D ratio: >1.5 broad buying, <0.67 broad selling
      FII net: >0 bullish, <0 bearish

    Returns composite score, label, and per-component breakdown.
    """
    score = 0.0
    components = {}

    # PCR scoring
    if pcr > 1.3:
        pcr_score = 2
        pcr_label = "EXTREMELY_BULLISH"
    elif pcr > 1.1:
        pcr_score = 1
        pcr_label = "BULLISH"
    elif pcr > 0.9:
        pcr_score = 0
        pcr_label = "NEUTRAL"
    elif pcr > 0.7:
        pcr_score = -1
        pcr_label = "BEARISH"
    else:
        pcr_score = -2
        pcr_label = "EXTREMELY_BEARISH"
    score += pcr_score
    components["pcr"] = {"value": pcr, "score": pcr_score, "label": pcr_label}

    # VIX scoring (India VIX ~11-30 typical range)
    if vix < 13:
        vix_score = 1
        vix_label = "LOW_FEAR"
    elif vix < 18:
        vix_score = 0
        vix_label = "NORMAL"
    elif vix < 22:
        vix_score = -1
        vix_label = "ELEVATED"
    else:
        vix_score = -2
        vix_label = "HIGH_FEAR"
    score += vix_score
    components["vix"] = {"value": vix, "score": vix_score, "label": vix_label}

    # A/D scoring
    if advance_decline_ratio > 2.0:
        ad_score = 2
        ad_label = "BROAD_BUYING"
    elif advance_decline_ratio > 1.5:
        ad_score = 1
        ad_label = "POSITIVE"
    elif advance_decline_ratio > 0.67:
        ad_score = 0
        ad_label = "NEUTRAL"
    elif advance_decline_ratio > 0.5:
        ad_score = -1
        ad_label = "NEGATIVE"
    else:
        ad_score = -2
        ad_label = "BROAD_SELLING"
    score += ad_score
    components["advance_decline"] = {
        "value": advance_decline_ratio,
        "score": ad_score,
        "label": ad_label,
    }

    # FII net scoring
    if fii_net > 2000:
        fii_score = 2
        fii_label = "STRONG_BUYING"
    elif fii_net > 0:
        fii_score = 1
        fii_label = "BUYING"
    elif fii_net > -2000:
        fii_score = -1
        fii_label = "SELLING"
    else:
        fii_score = -2
        fii_label = "STRONG_SELLING"
    score += fii_score
    components["fii"] = {"value": fii_net, "score": fii_score, "label": fii_label}

    # Trend bonus
    if trend == "BULLISH":
        score += 0.5
    elif trend == "BEARISH":
        score -= 0.5

    # Normalize to percentage (max possible is 8.5, min is -8.5)
    max_score = 8.5
    normalized = round((score / max_score) * 100, 1)

    # Label
    if score >= 4:
        label = "STRONGLY_BULLISH"
        gauge_value = min(90, 50 + normalized * 0.4)
    elif score >= 2:
        label = "BULLISH"
        gauge_value = min(75, 50 + normalized * 0.4)
    elif score >= -1:
        label = "NEUTRAL"
        gauge_value = 50.0
    elif score >= -3:
        label = "BEARISH"
        gauge_value = max(25, 50 + normalized * 0.4)
    else:
        label = "STRONGLY_BEARISH"
        gauge_value = max(10, 50 + normalized * 0.4)

    return {
        "score": round(score, 2),
        "normalized_score": normalized,
        "label": label,
        "gauge_value": round(gauge_value, 1),  # 0-100 for gauge display
        "components": components,
    }


# ---------------------------------------------------------------------------
# Open Interest Trend Analysis
# ---------------------------------------------------------------------------

def analyze_open_interest_trends(
    futures_oi: float,
    prev_oi: float,
    price_change: float,
) -> Dict[str, str]:
    """
    Classic OI interpretation based on price vs OI movement.

    Price up + OI up   = Long Buildup (bullish)
    Price up + OI down = Short Covering (bullish short-term)
    Price down + OI up = Short Buildup (bearish)
    Price down + OI down = Long Unwinding (bearish short-term)

    Returns interpretation, strength, and bias.
    """
    oi_change = futures_oi - prev_oi
    oi_change_pct = (oi_change / prev_oi * 100) if prev_oi else 0

    if price_change > 0 and oi_change > 0:
        interpretation = "LONG_BUILDUP"
        description = "Price rising with OI addition — fresh longs being added"
        bias = "BULLISH"
    elif price_change > 0 and oi_change < 0:
        interpretation = "SHORT_COVERING"
        description = "Price rising with OI reduction — shorts being covered"
        bias = "BULLISH"
    elif price_change < 0 and oi_change > 0:
        interpretation = "SHORT_BUILDUP"
        description = "Price falling with OI addition — fresh shorts being added"
        bias = "BEARISH"
    elif price_change < 0 and oi_change < 0:
        interpretation = "LONG_UNWINDING"
        description = "Price falling with OI reduction — longs exiting"
        bias = "BEARISH"
    else:
        interpretation = "NEUTRAL"
        description = "No significant OI or price movement"
        bias = "NEUTRAL"

    # Strength based on magnitude of OI change
    abs_oi_pct = abs(oi_change_pct)
    if abs_oi_pct > 10:
        strength = "STRONG"
    elif abs_oi_pct > 5:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    return {
        "interpretation": interpretation,
        "description": description,
        "bias": bias,
        "strength": strength,
        "oi_change": round(oi_change, 0),
        "oi_change_pct": round(oi_change_pct, 2),
        "price_change": round(price_change, 2),
    }
