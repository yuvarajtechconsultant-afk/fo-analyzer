"""
Options Analysis Engine
Black-Scholes greeks, IV calculation, PCR, max pain, strategy suggestions.
"""
import math
import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq
from typing import Dict, List, Optional, Any


# ---------------------------------------------------------------------------
# Black-Scholes Core
# ---------------------------------------------------------------------------

def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """BS d1 parameter."""
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """BS d2 parameter."""
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "CE"
) -> float:
    """Black-Scholes option price."""
    if T <= 0:
        if option_type.upper() == "CE":
            return max(S - K, 0)
        return max(K - S, 0)
    if sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)
    if option_type.upper() == "CE":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------

def calculate_greeks(
    spot: float,
    strike: float,
    expiry_days: float,
    iv: float,
    risk_free: float = 0.065,
    option_type: str = "CE",
) -> Dict[str, float]:
    """
    Calculate Black-Scholes option greeks.

    Parameters
    ----------
    spot        : Current spot price
    strike      : Strike price
    expiry_days : Calendar days to expiry
    iv          : Implied volatility as fraction (e.g. 0.15 for 15%)
    risk_free   : Risk-free rate as fraction
    option_type : 'CE' or 'PE'

    Returns
    -------
    Dict with: delta, gamma, theta, vega, rho, price, intrinsic, time_value
    """
    T = max(expiry_days / 365.0, 1e-6)
    sigma = max(iv, 1e-6)
    S, K, r = spot, strike, risk_free

    try:
        d1 = _d1(S, K, T, r, sigma)
        d2 = _d2(S, K, T, r, sigma)
        nd1 = norm.cdf(d1)
        nd2 = norm.cdf(d2)
        pdf_d1 = norm.pdf(d1)
        sqrt_T = math.sqrt(T)
        exp_rT = math.exp(-r * T)

        # Delta
        if option_type.upper() == "CE":
            delta = nd1
        else:
            delta = nd1 - 1

        # Gamma (same for CE and PE)
        gamma = pdf_d1 / (S * sigma * sqrt_T)

        # Theta (per day)
        theta_common = -(S * pdf_d1 * sigma) / (2 * sqrt_T)
        if option_type.upper() == "CE":
            theta = (theta_common - r * K * exp_rT * nd2) / 365
        else:
            theta = (theta_common + r * K * exp_rT * (1 - nd2)) / 365

        # Vega (per 1% change in IV)
        vega = S * sqrt_T * pdf_d1 / 100

        # Rho (per 1% change in rate)
        if option_type.upper() == "CE":
            rho = K * T * exp_rT * nd2 / 100
        else:
            rho = -K * T * exp_rT * (1 - nd2) / 100

        # Price
        price = bs_price(S, K, T, r, sigma, option_type)

        # Intrinsic value
        if option_type.upper() == "CE":
            intrinsic = max(S - K, 0)
        else:
            intrinsic = max(K - S, 0)

        time_value = max(price - intrinsic, 0)

        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
            "price": round(price, 2),
            "intrinsic": round(intrinsic, 2),
            "time_value": round(time_value, 2),
        }
    except Exception:
        return {
            "delta": 0.5 if option_type.upper() == "CE" else -0.5,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
            "price": 0.0,
            "intrinsic": max(spot - strike, 0) if option_type.upper() == "CE" else max(strike - spot, 0),
            "time_value": 0.0,
        }


# ---------------------------------------------------------------------------
# Implied Volatility
# ---------------------------------------------------------------------------

def calculate_iv(
    option_price: float,
    spot: float,
    strike: float,
    expiry_days: float,
    risk_free: float = 0.065,
    option_type: str = "CE",
) -> float:
    """
    Calculate Implied Volatility using Brent's method.
    Returns IV as fraction (e.g. 0.18 for 18%). Returns 0.0 if not solvable.
    """
    T = max(expiry_days / 365.0, 1e-6)
    if option_price <= 0 or T <= 0:
        return 0.0

    # Intrinsic check
    if option_type.upper() == "CE":
        intrinsic = max(spot - strike * math.exp(-risk_free * T), 0)
    else:
        intrinsic = max(strike * math.exp(-risk_free * T) - spot, 0)

    if option_price < intrinsic:
        return 0.0

    def objective(sigma: float) -> float:
        return bs_price(spot, strike, T, risk_free, sigma, option_type) - option_price

    try:
        iv = brentq(objective, 1e-6, 5.0, xtol=1e-6, maxiter=200)
        return round(iv, 6)
    except (ValueError, RuntimeError):
        return 0.0


# ---------------------------------------------------------------------------
# Option Chain Analytics
# ---------------------------------------------------------------------------

def calculate_pcr(option_chain: List[Dict]) -> Dict[str, float]:
    """
    Calculate Put-Call Ratio by OI and Volume.
    option_chain: list of {strike, CE: {oi, volume}, PE: {oi, volume}}
    """
    total_ce_oi = sum(
        row["CE"]["oi"] for row in option_chain if row.get("CE") and row["CE"].get("oi")
    )
    total_pe_oi = sum(
        row["PE"]["oi"] for row in option_chain if row.get("PE") and row["PE"].get("oi")
    )
    total_ce_vol = sum(
        row["CE"]["volume"] for row in option_chain if row.get("CE") and row["CE"].get("volume")
    )
    total_pe_vol = sum(
        row["PE"]["volume"] for row in option_chain if row.get("PE") and row["PE"].get("volume")
    )

    pcr_oi = round(total_pe_oi / total_ce_oi, 4) if total_ce_oi > 0 else 1.0
    pcr_vol = round(total_pe_vol / total_ce_vol, 4) if total_ce_vol > 0 else 1.0

    return {
        "pcr_oi": pcr_oi,
        "pcr_vol": pcr_vol,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "total_ce_vol": total_ce_vol,
        "total_pe_vol": total_pe_vol,
        "sentiment": _pcr_sentiment(pcr_oi),
    }


def _pcr_sentiment(pcr_oi: float) -> str:
    if pcr_oi > 1.3:
        return "EXTREMELY_BULLISH"
    elif pcr_oi > 1.1:
        return "BULLISH"
    elif pcr_oi > 0.9:
        return "NEUTRAL"
    elif pcr_oi > 0.7:
        return "BEARISH"
    return "EXTREMELY_BEARISH"


def calculate_max_pain(option_chain: List[Dict]) -> float:
    """
    Max pain strike: strike at which option writers (sellers) lose the least.
    For each strike S*, compute total pain = sum over all strikes K of:
      CE writers pain: sum of max(S* - K, 0) * CE_OI[K]
      PE writers pain: sum of max(K - S*, 0) * PE_OI[K]
    Returns the strike S* that minimizes total pain.
    """
    strikes = []
    ce_oi = {}
    pe_oi = {}

    for row in option_chain:
        strike = row["strike"]
        strikes.append(strike)
        ce_oi[strike] = row["CE"]["oi"] if row.get("CE") and row["CE"] else 0
        pe_oi[strike] = row["PE"]["oi"] if row.get("PE") and row["PE"] else 0

    if not strikes:
        return 0.0

    min_pain = float("inf")
    max_pain_strike = strikes[0]

    for test_strike in strikes:
        pain = 0.0
        for k in strikes:
            # CE loss for writers if test_strike > K
            pain += max(test_strike - k, 0) * ce_oi.get(k, 0)
            # PE loss for writers if test_strike < K
            pain += max(k - test_strike, 0) * pe_oi.get(k, 0)
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test_strike

    return float(max_pain_strike)


def analyze_oi_buildup(option_chain: List[Dict]) -> Dict[str, Any]:
    """
    Analyze OI change vs price change to determine buildup patterns.
    Returns dict with CE/PE buildup signals and top OI change strikes.
    """
    ce_oi_changes = []
    pe_oi_changes = []

    for row in option_chain:
        strike = row["strike"]
        if row.get("CE") and row["CE"]:
            ce = row["CE"]
            oi_chg = ce.get("oi_change", 0)
            ltp = ce.get("ltp", 0)
            ce_oi_changes.append({
                "strike": strike,
                "oi_change": oi_chg,
                "ltp": ltp,
                "type": "CE",
            })
        if row.get("PE") and row["PE"]:
            pe = row["PE"]
            oi_chg = pe.get("oi_change", 0)
            ltp = pe.get("ltp", 0)
            pe_oi_changes.append({
                "strike": strike,
                "oi_change": oi_chg,
                "ltp": ltp,
                "type": "PE",
            })

    # Sort by absolute OI change
    top_ce = sorted(ce_oi_changes, key=lambda x: abs(x["oi_change"]), reverse=True)[:5]
    top_pe = sorted(pe_oi_changes, key=lambda x: abs(x["oi_change"]), reverse=True)[:5]

    # Resistance: strike with highest CE OI (call writers defending)
    ce_by_oi = sorted(ce_oi_changes, key=lambda x: x.get("oi_change", 0), reverse=True)
    pe_by_oi = sorted(pe_oi_changes, key=lambda x: x.get("oi_change", 0), reverse=True)

    call_resistance = ce_by_oi[0]["strike"] if ce_by_oi else None
    put_support = pe_by_oi[0]["strike"] if pe_by_oi else None

    return {
        "top_ce_oi_change": top_ce,
        "top_pe_oi_change": top_pe,
        "call_resistance": call_resistance,
        "put_support": put_support,
    }


def get_iv_rank(
    current_iv: float,
    iv_history: List[float],
) -> Dict[str, float]:
    """
    Calculate IV Rank and IV Percentile.
    IV Rank: (current - 52w low) / (52w high - 52w low) * 100
    IV Percentile: % of days in last year when IV was lower than current
    """
    if not iv_history:
        return {"iv_rank": 50.0, "iv_percentile": 50.0, "iv_high": current_iv, "iv_low": current_iv}

    iv_high = max(iv_history)
    iv_low = min(iv_history)

    if iv_high == iv_low:
        iv_rank = 50.0
    else:
        iv_rank = round((current_iv - iv_low) / (iv_high - iv_low) * 100, 2)

    iv_percentile = round(
        sum(1 for v in iv_history if v < current_iv) / len(iv_history) * 100, 2
    )

    return {
        "iv_rank": max(0.0, min(100.0, iv_rank)),
        "iv_percentile": iv_percentile,
        "iv_high": round(iv_high * 100, 2),
        "iv_low": round(iv_low * 100, 2),
        "current_iv_pct": round(current_iv * 100, 2),
    }


def calculate_expected_move(
    spot: float,
    atm_iv: float,
    days_to_expiry: float,
) -> Dict[str, float]:
    """
    Calculate 1 standard deviation expected move by expiry.
    Expected move = spot * IV * sqrt(DTE / 365)
    Returns upper, lower, and pct_move.
    """
    if days_to_expiry <= 0 or atm_iv <= 0:
        return {"upper": spot, "lower": spot, "pct_move": 0.0, "points": 0.0}

    pct_move = atm_iv * math.sqrt(days_to_expiry / 365.0)
    points = spot * pct_move
    return {
        "upper": round(spot + points, 2),
        "lower": round(spot - points, 2),
        "pct_move": round(pct_move * 100, 2),
        "points": round(points, 2),
    }


# ---------------------------------------------------------------------------
# Strategy Suggestions
# ---------------------------------------------------------------------------

def suggest_strategies(
    spot: float,
    pcr: float,
    iv_rank: float,
    trend: str,
    expiry_days: int,
    atm_strike: float = None,
) -> List[Dict[str, Any]]:
    """
    Suggest appropriate F&O strategies based on market conditions.

    Parameters
    ----------
    spot        : Current spot price
    pcr         : Put-Call ratio (OI based)
    iv_rank     : IV rank 0-100
    trend       : 'BULLISH', 'BEARISH', or 'NEUTRAL'
    expiry_days : Days to expiry
    atm_strike  : ATM strike (defaults to nearest round number to spot)
    """
    if atm_strike is None:
        atm_strike = round(spot / 50) * 50

    strategies = []
    is_high_iv = iv_rank > 60
    is_low_iv = iv_rank < 40

    # -------------------------------------------------------------------
    # Bullish strategies
    # -------------------------------------------------------------------
    if trend in ("BULLISH",) and not is_high_iv:
        # Bull Call Spread
        buy_strike = atm_strike
        sell_strike = atm_strike + 100
        max_profit_est = sell_strike - buy_strike  # rough
        strategies.append({
            "name": "Bull Call Spread",
            "type": "DEBIT_SPREAD",
            "direction": "BULLISH",
            "legs": [
                {"action": "BUY", "type": "CE", "strike": buy_strike, "qty": 1},
                {"action": "SELL", "type": "CE", "strike": sell_strike, "qty": 1},
            ],
            "max_profit": f"~{sell_strike - buy_strike} pts",
            "max_loss": "Premium paid",
            "ideal_condition": "Moderately bullish, low IV",
            "risk_reward": "1:2",
            "confidence": "HIGH" if pcr > 1.1 else "MEDIUM",
        })

    if trend == "BULLISH" and not is_low_iv:
        # Naked CE buy (only when trend strong)
        strategies.append({
            "name": "ATM Call Buy",
            "type": "NAKED_BUY",
            "direction": "BULLISH",
            "legs": [{"action": "BUY", "type": "CE", "strike": atm_strike, "qty": 1}],
            "max_profit": "Unlimited",
            "max_loss": "Premium paid",
            "ideal_condition": "Strong uptrend, breakout expected",
            "risk_reward": "1:3+",
            "confidence": "MEDIUM",
        })

    # -------------------------------------------------------------------
    # Bearish strategies
    # -------------------------------------------------------------------
    if trend in ("BEARISH",) and not is_high_iv:
        buy_strike = atm_strike
        sell_strike = atm_strike - 100
        strategies.append({
            "name": "Bear Put Spread",
            "type": "DEBIT_SPREAD",
            "direction": "BEARISH",
            "legs": [
                {"action": "BUY", "type": "PE", "strike": buy_strike, "qty": 1},
                {"action": "SELL", "type": "PE", "strike": sell_strike, "qty": 1},
            ],
            "max_profit": f"~{buy_strike - sell_strike} pts",
            "max_loss": "Premium paid",
            "ideal_condition": "Moderately bearish, low IV",
            "risk_reward": "1:2",
            "confidence": "HIGH" if pcr < 0.9 else "MEDIUM",
        })

    if trend == "BEARISH" and not is_low_iv:
        strategies.append({
            "name": "ATM Put Buy",
            "type": "NAKED_BUY",
            "direction": "BEARISH",
            "legs": [{"action": "BUY", "type": "PE", "strike": atm_strike, "qty": 1}],
            "max_profit": "Unlimited (to 0)",
            "max_loss": "Premium paid",
            "ideal_condition": "Strong downtrend, breakdown expected",
            "risk_reward": "1:3+",
            "confidence": "MEDIUM",
        })

    # -------------------------------------------------------------------
    # Neutral / High IV strategies
    # -------------------------------------------------------------------
    if is_high_iv and expiry_days <= 15:
        # Short Straddle
        strategies.append({
            "name": "Short Straddle",
            "type": "CREDIT_SPREAD",
            "direction": "NEUTRAL",
            "legs": [
                {"action": "SELL", "type": "CE", "strike": atm_strike, "qty": 1},
                {"action": "SELL", "type": "PE", "strike": atm_strike, "qty": 1},
            ],
            "max_profit": "Total premium received",
            "max_loss": "Unlimited",
            "ideal_condition": "High IV, low directional move expected",
            "risk_reward": "Variable",
            "confidence": "HIGH" if 0.85 < pcr < 1.15 else "MEDIUM",
        })

        # Iron Condor
        strategies.append({
            "name": "Iron Condor",
            "type": "CREDIT_SPREAD",
            "direction": "NEUTRAL",
            "legs": [
                {"action": "SELL", "type": "CE", "strike": atm_strike + 100, "qty": 1},
                {"action": "BUY", "type": "CE", "strike": atm_strike + 200, "qty": 1},
                {"action": "SELL", "type": "PE", "strike": atm_strike - 100, "qty": 1},
                {"action": "BUY", "type": "PE", "strike": atm_strike - 200, "qty": 1},
            ],
            "max_profit": "Net premium received",
            "max_loss": "Wing spread - net premium",
            "ideal_condition": "High IV, range-bound market",
            "risk_reward": "1:1.5",
            "confidence": "HIGH" if iv_rank > 70 else "MEDIUM",
        })

    if is_low_iv and expiry_days >= 10:
        # Long Straddle
        strategies.append({
            "name": "Long Straddle",
            "type": "DEBIT_SPREAD",
            "direction": "NEUTRAL_VOLATILE",
            "legs": [
                {"action": "BUY", "type": "CE", "strike": atm_strike, "qty": 1},
                {"action": "BUY", "type": "PE", "strike": atm_strike, "qty": 1},
            ],
            "max_profit": "Unlimited",
            "max_loss": "Total premium paid",
            "ideal_condition": "Low IV, big move expected (event/results)",
            "risk_reward": "1:3+",
            "confidence": "MEDIUM",
        })

    if trend == "BULLISH" and is_high_iv:
        # Bull Put Spread (credit)
        strategies.append({
            "name": "Bull Put Spread",
            "type": "CREDIT_SPREAD",
            "direction": "BULLISH",
            "legs": [
                {"action": "SELL", "type": "PE", "strike": atm_strike - 50, "qty": 1},
                {"action": "BUY", "type": "PE", "strike": atm_strike - 150, "qty": 1},
            ],
            "max_profit": "Net premium received",
            "max_loss": "Spread - premium",
            "ideal_condition": "Bullish bias, high IV to collect premium",
            "risk_reward": "1:2",
            "confidence": "HIGH" if pcr > 1.2 else "MEDIUM",
        })

    if trend == "BEARISH" and is_high_iv:
        # Bear Call Spread (credit)
        strategies.append({
            "name": "Bear Call Spread",
            "type": "CREDIT_SPREAD",
            "direction": "BEARISH",
            "legs": [
                {"action": "SELL", "type": "CE", "strike": atm_strike + 50, "qty": 1},
                {"action": "BUY", "type": "CE", "strike": atm_strike + 150, "qty": 1},
            ],
            "max_profit": "Net premium received",
            "max_loss": "Spread - premium",
            "ideal_condition": "Bearish bias, high IV to collect premium",
            "risk_reward": "1:2",
            "confidence": "HIGH" if pcr < 0.8 else "MEDIUM",
        })

    # Always add a calendar note if no strategies fit
    if not strategies:
        strategies.append({
            "name": "Wait & Watch",
            "type": "NO_TRADE",
            "direction": "NEUTRAL",
            "legs": [],
            "max_profit": "N/A",
            "max_loss": "N/A",
            "ideal_condition": "Market conditions unclear, better to wait",
            "risk_reward": "N/A",
            "confidence": "HIGH",
        })

    return strategies


def enrich_option_chain_with_greeks(
    option_chain: List[Dict],
    spot: float,
    expiry_days: float,
    risk_free: float = 0.065,
) -> List[Dict]:
    """
    Add IV and greeks to each option in the chain.
    Modifies in-place and returns the chain.
    """
    for row in option_chain:
        for opt_type in ("CE", "PE"):
            opt = row.get(opt_type)
            if not opt or not isinstance(opt, dict):
                row[opt_type] = None
                continue
            try:
                ltp = opt.get("ltp", 0) or 0
                if ltp <= 0:
                    opt["iv"] = 0.0
                    opt["greeks"] = {}
                    continue
                iv = calculate_iv(ltp, spot, row["strike"], expiry_days, risk_free, opt_type)
                opt["iv"] = round(iv * 100, 2)
                opt["greeks"] = calculate_greeks(
                    spot, row["strike"], expiry_days, iv, risk_free, opt_type
                )
            except Exception:
                opt["iv"] = 0.0
                opt["greeks"] = {}
    return option_chain
