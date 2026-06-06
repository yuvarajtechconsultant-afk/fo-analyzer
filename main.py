"""
F&O Analyzer — FastAPI Application
Zerodha-powered Futures & Options analysis for NIFTY and SENSEX.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional

import math
import random
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware


def sanitize(obj):
    """Recursively replace NaN/Infinity floats with None so JSON serialization never fails."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return sanitize(obj.tolist())
    return obj

from config import (
    NIFTY_TOKEN, SENSEX_TOKEN, get_expiry_list, get_nearest_expiry,
    get_atm_strike, days_to_expiry, LOT_SIZES, RISK_FREE_RATE,
)
from zerodha_client import get_client, set_access_token, ZerodhaClient
from analysis.technical import generate_technical_signals
from analysis.options import (
    calculate_pcr, calculate_max_pain, analyze_oi_buildup,
    get_iv_rank, calculate_expected_move, suggest_strategies,
    enrich_option_chain_with_greeks,
)
from analysis.market_breadth import (
    get_fii_dii_activity, calculate_advance_decline,
    get_market_sentiment, analyze_open_interest_trends,
)
from analysis.signals import generate_trade_signals, format_signal_for_display

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Environment ───────────────────────────────────────────────
APP_ENV  = os.getenv("APP_ENV", "development")
APP_HOST = os.getenv("APP_HOST", "http://localhost:8000")
IS_PROD  = APP_ENV == "production"

app = FastAPI(
    title="F&O Analyzer",
    description="Sensex & Nifty Futures and Options Analysis Dashboard",
    version="1.0.0",
    docs_url=None if IS_PROD else "/docs",   # hide Swagger in prod
    redoc_url=None if IS_PROD else "/redoc",
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static files and templates ────────────────────────────────
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Utility helpers  (delegate to rich mock_data module)
# ---------------------------------------------------------------------------
from mock_data import (
    get_mock_historical          as _mock_historical_rich,
    get_mock_quote               as _mock_quote_rich,
    get_mock_option_chain        as _mock_option_chain,
    get_mock_option_chain_history as _mock_option_chain_history,
    get_mock_fii_dii             as _mock_fii_dii,
    get_mock_market_breadth      as _mock_market_breadth,
)

def _mock_historical(index: str, interval: str, days: int) -> List[Dict]:
    """1-month realistic OHLCV mock — GBM simulation with IV smile."""
    return _mock_historical_rich(index, interval, days)


def _mock_quote(index: str) -> Dict:
    """Realistic spot quote with OHLC."""
    return _mock_quote_rich(index)


def _get_historical_df(index: str, interval: str = "5minute", days: int = 10) -> pd.DataFrame:
    """Fetch historical data and return as DataFrame."""
    client = get_client()
    try:
        if not client.is_authenticated():
            candles = _mock_historical(index, interval, days)
        else:
            candles = client.get_index_historical(index, interval=interval, days=days)
    except Exception as e:
        logger.warning("Historical data fetch failed, using mock: %s", e)
        candles = _mock_historical(index, interval, days)

    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check — used by hosting platforms."""
    return {"status": "ok", "env": APP_ENV, "timestamp": datetime.now().isoformat()}


@app.get("/api/config")
async def app_config():
    """Return public app config to the frontend (no secrets)."""
    return {
        "app_host": APP_HOST,
        "env":      APP_ENV,
        "is_prod":  IS_PROD,
    }


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main dashboard."""
    return FileResponse("templates/index.html")


@app.get("/api/auth/login")
async def auth_login():
    """Get Zerodha login URL."""
    client = get_client()
    login_url = client.get_login_url()
    return {"login_url": login_url}


@app.get("/api/auth/callback")
async def auth_callback(request_token: str):
    """Handle Zerodha OAuth callback — exchange request_token for access_token."""
    client = get_client()
    try:
        session_data = client.generate_session(request_token)
        set_access_token(session_data["access_token"])
        return JSONResponse({
            "status": "success",
            "message": f"Logged in as {session_data.get('user_name', 'User')}",
            "access_token": session_data["access_token"],
            "user_id": session_data.get("user_id"),
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")


@app.post("/api/postback")
async def postback(request: Request):
    """Zerodha postback endpoint — acknowledges order/trade updates."""
    try:
        data = await request.json()
        logger.info("Zerodha postback received: %s", data)
    except Exception:
        pass
    return JSONResponse({"status": "ok"})


@app.get("/api/auth/status")
async def auth_status():
    """Check authentication status."""
    client = get_client()
    return {"authenticated": client.is_authenticated()}


@app.get("/api/market/quote/{index}")
async def market_quote(index: str):
    """Get spot quote for NIFTY or SENSEX."""
    valid_indices = ["NIFTY", "SENSEX", "BANKNIFTY", "VIX"]
    if index.upper() not in valid_indices:
        raise HTTPException(status_code=400, detail=f"Invalid index. Choose from: {valid_indices}")

    client = get_client()
    try:
        if not client.is_authenticated():
            return _mock_quote(index)
        return client.get_index_quote(index)
    except Exception as e:
        logger.warning("Quote fetch failed: %s", e)
        return _mock_quote(index)


@app.get("/api/historical/{index}")
async def historical_data(index: str, interval: str = "5minute", days: int = 5):
    """Get historical OHLCV candles."""
    valid_intervals = ["minute", "3minute", "5minute", "15minute", "30minute", "60minute", "day"]
    if interval not in valid_intervals:
        raise HTTPException(status_code=400, detail=f"Invalid interval. Use: {valid_intervals}")

    df = _get_historical_df(index, interval, days)
    if df.empty:
        return {"candles": [], "index": index, "interval": interval}

    df_reset = df.reset_index()
    candles = []
    for _, row in df_reset.iterrows():
        candles.append({
            "time": int(row["date"].timestamp()),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
            "volume": int(row.get("volume", 0)),
        })

    return JSONResponse(sanitize({
        "index": index.upper(),
        "interval": interval,
        "candles": candles,
        "count": len(candles),
    }))


@app.get("/api/chart-signals/{index}")
async def chart_signals(index: str, interval: str = "15minute", days: int = 10):
    """
    Return candle-level BUY / SELL signal markers for the chart.
    Uses multi-indicator confluence: EMA cross, MACD cross, RSI extremes, Supertrend flip.
    """
    from analysis.technical import (
        calculate_ema, calculate_rsi, calculate_macd,
        calculate_supertrend, calculate_atr,
    )

    df = _get_historical_df(index, interval, days)
    if df.empty or len(df) < 30:
        return JSONResponse({"signals": []})

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]

    # ── Indicators ────────────────────────────────────────────
    ema9   = calculate_ema(close, 9)
    ema21  = calculate_ema(close, 21)
    ema50  = calculate_ema(close, 50)
    rsi    = calculate_rsi(close, 14)
    macd_d = calculate_macd(close)
    macd_l = macd_d["macd"]
    sig_l  = macd_d["signal"]
    hist   = macd_d["histogram"]

    try:
        st_data = calculate_supertrend(high, low, close, period=10, multiplier=3)
        st_dir  = st_data.get("direction", pd.Series([1]*len(df), index=df.index))
    except Exception:
        st_dir = pd.Series([1]*len(df), index=df.index)

    signals = []
    df_reset = df.reset_index()

    for i in range(2, len(df_reset)):
        row      = df_reset.iloc[i]
        prev     = df_reset.iloc[i - 1]
        prev2    = df_reset.iloc[i - 2]
        ts       = int(row["date"].timestamp())
        c_price  = float(close.iloc[i])

        score_buy  = 0
        score_sell = 0
        reasons    = []

        # 1. EMA 9/21 crossover
        if (float(ema9.iloc[i-1]) <= float(ema21.iloc[i-1]) and
                float(ema9.iloc[i]) > float(ema21.iloc[i])):
            score_buy += 2
            reasons.append("EMA9 crossed above EMA21")
        if (float(ema9.iloc[i-1]) >= float(ema21.iloc[i-1]) and
                float(ema9.iloc[i]) < float(ema21.iloc[i])):
            score_sell += 2
            reasons.append("EMA9 crossed below EMA21")

        # 2. Price crosses EMA50
        if float(prev["close"]) < float(ema50.iloc[i-1]) and c_price > float(ema50.iloc[i]):
            score_buy += 1
            reasons.append("Price crossed above EMA50")
        if float(prev["close"]) > float(ema50.iloc[i-1]) and c_price < float(ema50.iloc[i]):
            score_sell += 1
            reasons.append("Price crossed below EMA50")

        # 3. MACD histogram flip
        if float(hist.iloc[i-1]) < 0 and float(hist.iloc[i]) >= 0:
            score_buy += 2
            reasons.append("MACD histogram turned positive")
        if float(hist.iloc[i-1]) > 0 and float(hist.iloc[i]) <= 0:
            score_sell += 2
            reasons.append("MACD histogram turned negative")

        # 4. RSI zone
        rsi_val = float(rsi.iloc[i])
        rsi_prev = float(rsi.iloc[i-1])
        if rsi_prev < 35 and rsi_val >= 35:
            score_buy += 2
            reasons.append(f"RSI recovered from oversold ({rsi_val:.0f})")
        if rsi_prev > 65 and rsi_val <= 65:
            score_sell += 2
            reasons.append(f"RSI dropped from overbought ({rsi_val:.0f})")
        if rsi_val < 25:
            score_buy += 1
            reasons.append("RSI deeply oversold")
        if rsi_val > 75:
            score_sell += 1
            reasons.append("RSI deeply overbought")

        # 5. Supertrend direction flip
        try:
            st_now  = int(st_dir.iloc[i])
            st_prev = int(st_dir.iloc[i-1])
            if st_prev == -1 and st_now == 1:
                score_buy += 3
                reasons.append("Supertrend flipped BULLISH")
            if st_prev == 1 and st_now == -1:
                score_sell += 3
                reasons.append("Supertrend flipped BEARISH")
        except Exception:
            pass

        # ── Emit signal if score ≥ 3 ─────────────────────────
        if score_buy >= 3 and score_buy > score_sell:
            signals.append({
                "time":      ts,
                "type":      "BUY",
                "price":     round(c_price, 2),
                "score":     score_buy,
                "strength":  "STRONG" if score_buy >= 5 else "MODERATE",
                "reasons":   reasons,
            })
        elif score_sell >= 3 and score_sell > score_buy:
            signals.append({
                "time":      ts,
                "type":      "SELL",
                "price":     round(c_price, 2),
                "score":     score_sell,
                "strength":  "STRONG" if score_sell >= 5 else "MODERATE",
                "reasons":   reasons,
            })

    return JSONResponse(sanitize({"signals": signals, "count": len(signals)}))


@app.get("/api/analysis/{index}")
async def full_analysis(index: str, interval: str = "15minute", days: int = 10):
    """Full technical + options analysis with trade signals."""
    index = index.upper()

    # Fetch spot price
    client = get_client()
    try:
        if client.is_authenticated():
            quote = client.get_index_quote(index)
        else:
            quote = _mock_quote(index)
        spot = quote["last_price"]
    except Exception:
        quote = _mock_quote(index)
        spot = quote["last_price"]

    # Technical analysis
    df = _get_historical_df(index, interval, days)
    if not df.empty:
        tech = generate_technical_signals(df)
    else:
        from analysis.technical import _empty_signals
        tech = _empty_signals()
        tech["current_price"] = spot

    # Options analysis (use mock PCR/max pain if no API)
    expiry = get_nearest_expiry(index).strftime("%Y-%m-%d")
    dte = days_to_expiry(expiry)
    atm = get_atm_strike(spot, index)

    options_data: Dict[str, Any] = {}
    try:
        if client.is_authenticated():
            chain_data = client.get_option_chain(index, expiry)
            chain = chain_data.get("chain", [])
            chain = enrich_option_chain_with_greeks(chain, spot, dte, RISK_FREE_RATE)
            pcr_data = calculate_pcr(chain)
            max_pain = calculate_max_pain(chain)
            oi_analysis = analyze_oi_buildup(chain)

            # Get ATM IV for expected move
            atm_iv = 0.15  # default
            for row in chain:
                if row["strike"] == atm and row.get("CE") and row["CE"]:
                    atm_iv = row["CE"].get("iv", 15) / 100
                    break
        else:
            import random
            pcr_data = {
                "pcr_oi": round(0.8 + random.random() * 0.8, 3),
                "pcr_vol": round(0.8 + random.random() * 0.8, 3),
                "total_ce_oi": 0, "total_pe_oi": 0,
                "total_ce_vol": 0, "total_pe_vol": 0,
                "sentiment": "NEUTRAL",
            }
            max_pain = atm
            oi_analysis = {"top_ce_oi_change": [], "top_pe_oi_change": [], "call_resistance": atm + 100, "put_support": atm - 100}
            atm_iv = 0.15

        iv_rank_data = get_iv_rank(atm_iv, [atm_iv * (0.8 + i * 0.02) for i in range(20)])
        expected_move = calculate_expected_move(spot, atm_iv, dte)

        options_data = {
            "pcr": pcr_data,
            "max_pain": max_pain,
            "oi_trend": oi_analysis,
            "iv_rank": iv_rank_data,
            "expected_move": expected_move,
            "expiry": expiry,
            "dte": dte,
            "atm_iv": round(atm_iv * 100, 2),
        }
    except Exception as e:
        logger.warning("Options analysis error: %s", e)
        options_data = {
            "pcr": {"pcr_oi": 1.0, "sentiment": "NEUTRAL"},
            "max_pain": atm,
            "oi_trend": {},
            "iv_rank": {"iv_rank": 50},
            "expected_move": {"upper": spot + 100, "lower": spot - 100, "pct_move": 1.0},
            "expiry": expiry,
            "dte": dte,
        }

    # Sentiment
    pcr_val = options_data.get("pcr", {}).get("pcr_oi", 1.0)
    vix_quote = _mock_quote("VIX")
    try:
        if client.is_authenticated():
            vix_q = client.get_index_quote("VIX")
            vix = vix_q.get("last_price", 14.5)
        else:
            vix = vix_quote["last_price"]
    except Exception:
        vix = 14.5

    sentiment = get_market_sentiment(
        pcr=pcr_val,
        vix=vix,
        advance_decline_ratio=1.2,
        fii_net=500,
        trend=tech.get("trend", "NEUTRAL"),
    )

    # Trade signals
    signal = generate_trade_signals(index, spot, tech, options_data, sentiment)
    signal = format_signal_for_display(signal)

    # Strategy suggestions
    trend_map = {"BULLISH": "BULLISH", "BEARISH": "BEARISH", "NEUTRAL": "NEUTRAL", "UPTREND": "BULLISH", "DOWNTREND": "BEARISH", "SIDEWAYS": "NEUTRAL"}
    strategies = suggest_strategies(
        spot=spot,
        pcr=pcr_val,
        iv_rank=options_data.get("iv_rank", {}).get("iv_rank", 50),
        trend=trend_map.get(tech.get("overall_bias", "NEUTRAL"), "NEUTRAL"),
        expiry_days=dte,
        atm_strike=atm,
    )

    return JSONResponse(sanitize({
        "index": index,
        "spot": spot,
        "timestamp": datetime.now().isoformat(),
        "technical": tech,
        "options": options_data,
        "sentiment": sentiment,
        "signal": signal,
        "strategies": strategies,
        "expiry_list": get_expiry_list(index, 5),
    }))


@app.get("/api/option-chain/{index}")
async def option_chain(index: str, expiry: Optional[str] = None):
    """Get full option chain with greeks."""
    index = index.upper()
    if not expiry:
        expiry = get_nearest_expiry(index).strftime("%Y-%m-%d")

    dte = days_to_expiry(expiry)
    client = get_client()

    try:
        if client.is_authenticated():
            chain_data = client.get_option_chain(index, expiry)
        else:
            # Rich 1-month mock option chain with IV smile & OI skew
            spot = _mock_quote(index)["last_price"]
            chain_data = _mock_option_chain(index, expiry, spot)

        # Enrich with greeks (safe — catch per-row errors)
        try:
            chain_data["chain"] = enrich_option_chain_with_greeks(
                chain_data["chain"], chain_data["spot"], dte, RISK_FREE_RATE
            )
        except Exception as eg:
            logger.warning("Greeks enrichment failed: %s", eg)

        # Add analytics
        chain = chain_data["chain"]
        pcr_data = calculate_pcr(chain)
        max_pain = calculate_max_pain(chain)
        oi_analysis = analyze_oi_buildup(chain)
        atm = get_atm_strike(chain_data["spot"], index)

        return JSONResponse(sanitize({
            **chain_data,
            "atm_strike": atm,
            "pcr": pcr_data,
            "max_pain": max_pain,
            "oi_analysis": oi_analysis,
            "dte": dte,
            "expiry_list": get_expiry_list(index, 5),
        }))

    except Exception as e:
        logger.error("Option chain error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/option-chain-history/{index}")
async def option_chain_history(index: str, expiry: Optional[str] = None, days: int = 22):
    """
    Return daily historical option chain snapshots for past N trading days.
    Shows PCR trend, max pain movement, IV change, and OI buildup over time.
    """
    index = index.upper()
    if not expiry:
        expiry = get_nearest_expiry(index).strftime("%Y-%m-%d")

    client = get_client()
    try:
        spot = _mock_quote(index)["last_price"]
        if client.is_authenticated():
            try:
                q = client.get_index_quote(index)
                spot = q["last_price"]
            except Exception:
                pass

        history = _mock_option_chain_history(index, expiry, spot, days=min(days, 22))

        # Summary trend stats across all days
        pcr_series    = [s["pcr_oi"]   for s in history]
        mp_series     = [s["max_pain"] for s in history]
        iv_series     = [s["atm_iv"]   for s in history]
        spot_series   = [s["spot"]     for s in history]

        return JSONResponse(sanitize({
            "index":   index,
            "expiry":  expiry,
            "days":    len(history),
            "history": history,
            "trend_summary": {
                "pcr_start":      pcr_series[0]  if pcr_series  else None,
                "pcr_end":        pcr_series[-1] if pcr_series  else None,
                "pcr_avg":        round(sum(pcr_series) / len(pcr_series), 3) if pcr_series else None,
                "max_pain_start": mp_series[0]   if mp_series   else None,
                "max_pain_end":   mp_series[-1]  if mp_series   else None,
                "iv_start":       iv_series[0]   if iv_series   else None,
                "iv_end":         iv_series[-1]  if iv_series   else None,
                "spot_start":     spot_series[0] if spot_series else None,
                "spot_end":       spot_series[-1]if spot_series else None,
                "spot_change_pct": round(
                    (spot_series[-1] - spot_series[0]) / spot_series[0] * 100, 2
                ) if spot_series else None,
            },
        }))
    except Exception as e:
        logger.error("Option chain history error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
async def positions():
    """Get open positions and orders."""
    client = get_client()
    if not client.is_authenticated():
        return {"net": [], "day": [], "orders": [], "authenticated": False}

    try:
        pos = client.get_positions()
        orders = client.get_orders()
        return {
            "net": pos.get("net", []),
            "day": pos.get("day", []),
            "orders": orders,
            "authenticated": True,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/order")
async def place_order(request: Request):
    """Place a new order."""
    client = get_client()
    if not client.is_authenticated():
        raise HTTPException(status_code=401, detail="Not authenticated with Zerodha")

    params = await request.json()
    try:
        order_id = client.place_order(params)
        return {"status": "success", "order_id": order_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/dashboard")
async def dashboard():
    """Combined dashboard data — NIFTY + SENSEX summary."""
    client = get_client()

    # Fetch both index quotes
    try:
        if client.is_authenticated():
            nifty_q = client.get_index_quote("NIFTY")
            sensex_q = client.get_index_quote("SENSEX")
        else:
            nifty_q = _mock_quote("NIFTY")
            sensex_q = _mock_quote("SENSEX")
    except Exception:
        nifty_q = _mock_quote("NIFTY")
        sensex_q = _mock_quote("SENSEX")

    try:
        if client.is_authenticated():
            vix_q = client.get_index_quote("VIX")
        else:
            vix_q = _mock_quote("VIX")
    except Exception:
        vix_q = _mock_quote("VIX")

    # Quick technical signals from daily data
    def quick_signal(index):
        try:
            df = _get_historical_df(index, "day", 60)
            if df.empty:
                return {"overall_bias": "NEUTRAL", "signal_score": 0}
            return generate_technical_signals(df)
        except Exception:
            return {"overall_bias": "NEUTRAL", "signal_score": 0}

    nifty_tech = quick_signal("NIFTY")
    sensex_tech = quick_signal("SENSEX")

    # Rich 1-month FII/DII mock data
    fii_dii = _mock_fii_dii()

    # Rich advance/decline breadth
    breadth = _mock_market_breadth()
    adv  = breadth["advances"]
    dec  = breadth["declines"]
    unch = breadth["unchanged"]
    ad_data = calculate_advance_decline(adv, dec, unch)
    ad_data.update(breadth)

    # Sentiment
    nifty_pcr = 0.8 + random.uniform(-0.2, 0.4)
    sentiment = get_market_sentiment(
        pcr=nifty_pcr,
        vix=vix_q.get("last_price", 14.5),
        advance_decline_ratio=ad_data["ratio"],
        fii_net=fii_dii.get("fii_net", 500),
        trend=nifty_tech.get("trend", "NEUTRAL"),
    )

    # Quick signals
    nifty_expiry = get_nearest_expiry("NIFTY").strftime("%Y-%m-%d")
    sensex_expiry = get_nearest_expiry("SENSEX").strftime("%Y-%m-%d")
    nifty_dte = days_to_expiry(nifty_expiry)
    sensex_dte = days_to_expiry(sensex_expiry)

    nifty_signal = generate_trade_signals(
        "NIFTY", nifty_q["last_price"], nifty_tech,
        {"pcr": {"pcr_oi": nifty_pcr}, "max_pain": get_atm_strike(nifty_q["last_price"], "NIFTY"), "oi_trend": {}, "iv_rank": {"iv_rank": 45}},
        sentiment,
    )
    sensex_signal = generate_trade_signals(
        "SENSEX", sensex_q["last_price"], sensex_tech,
        {"pcr": {"pcr_oi": nifty_pcr}, "max_pain": get_atm_strike(sensex_q["last_price"], "SENSEX"), "oi_trend": {}, "iv_rank": {"iv_rank": 45}},
        sentiment,
    )

    nifty_expected_move = calculate_expected_move(nifty_q["last_price"], 0.15, nifty_dte)
    sensex_expected_move = calculate_expected_move(sensex_q["last_price"], 0.14, sensex_dte)

    return JSONResponse(sanitize({
        "timestamp": datetime.now().isoformat(),
        "indices": {
            "nifty": nifty_q,
            "sensex": sensex_q,
            "vix": vix_q,
        },
        "sentiment": sentiment,
        "fii_dii": fii_dii,
        "advance_decline": ad_data,
        "nifty": {
            "signal": format_signal_for_display(nifty_signal),
            "technical": {
                "bias": nifty_tech.get("overall_bias", "NEUTRAL"),
                "trend": nifty_tech.get("trend", "SIDEWAYS"),
                "rsi": nifty_tech.get("rsi", 50),
                "score": nifty_tech.get("signal_score", 0),
            },
            "pcr": round(nifty_pcr, 3),
            "max_pain": get_atm_strike(nifty_q["last_price"], "NIFTY"),
            "expected_move": nifty_expected_move,
            "expiry": nifty_expiry,
            "dte": nifty_dte,
        },
        "sensex": {
            "signal": format_signal_for_display(sensex_signal),
            "technical": {
                "bias": sensex_tech.get("overall_bias", "NEUTRAL"),
                "trend": sensex_tech.get("trend", "SIDEWAYS"),
                "rsi": sensex_tech.get("rsi", 50),
                "score": sensex_tech.get("signal_score", 0),
            },
            "pcr": round(nifty_pcr * 0.98, 3),
            "max_pain": get_atm_strike(sensex_q["last_price"], "SENSEX"),
            "expected_move": sensex_expected_move,
            "expiry": sensex_expiry,
            "dte": sensex_dte,
        },
        "authenticated": client.is_authenticated(),
    }))


# ---------------------------------------------------------------------------
# WebSocket — Live Price Streaming
# ---------------------------------------------------------------------------

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """WebSocket endpoint that streams live quotes every 2 seconds."""
    await manager.connect(websocket)
    client = get_client()
    try:
        while True:
            try:
                if client.is_authenticated():
                    nifty = client.get_index_quote("NIFTY")
                    sensex = client.get_index_quote("SENSEX")
                    vix_data = client.get_index_quote("VIX")
                else:
                    nifty = _mock_quote("NIFTY")
                    sensex = _mock_quote("SENSEX")
                    vix_data = _mock_quote("VIX")
            except Exception:
                nifty = _mock_quote("NIFTY")
                sensex = _mock_quote("SENSEX")
                vix_data = _mock_quote("VIX")

            await websocket.send_json({
                "type": "live_quote",
                "timestamp": datetime.now().isoformat(),
                "nifty": {
                    "price": nifty["last_price"],
                    "change": nifty["change"],
                    "change_pct": nifty["change_pct"],
                },
                "sensex": {
                    "price": sensex["last_price"],
                    "change": sensex["change"],
                    "change_pct": sensex["change_pct"],
                },
                "vix": vix_data.get("last_price", 14.5),
            })
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        manager.disconnect(websocket)
