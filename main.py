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


def _get_option_chain_list(index: str, expiry: str):
    """Return (chain_list, spot) — handles both live and mock data formats."""
    from mock_data import get_mock_option_chain as _mock_oc
    client = get_client()
    default_spot = 22000 if index.upper() == "NIFTY" else 73000
    try:
        if client.is_authenticated():
            spot  = client.get_index_quote(index).get("last_price", default_spot)
            chain = client.get_option_chain(index, expiry)
            if isinstance(chain, dict):
                spot  = chain.get("spot", spot)
                chain = chain.get("chain", [])
            return chain, spot
    except Exception:
        pass
    raw = _mock_oc(index, expiry, None)
    if isinstance(raw, dict):
        return raw.get("chain", []), raw.get("spot", default_spot)
    return raw, default_spot


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


def _build_nfo_symbol(index: str, expiry_date, strike: int, opt_type: str) -> str:
    """
    Build Zerodha NFO/BFO trading symbol.
    NIFTY  weekly format: NIFTY25JUN22500CE  (NIFTYYYMMMDDDDDCE)
    SENSEX weekly format: SENSEX25JUN72100PE
    """
    month_map = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
                 7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
    yy    = str(expiry_date.year)[2:]
    mon   = month_map[expiry_date.month]
    day   = str(expiry_date.day).zfill(2)
    name  = "NIFTY" if index.upper() == "NIFTY" else "SENSEX"
    return f"{name}{yy}{mon}{day}{strike}{opt_type.upper()}"


@app.get("/api/algo-signals")
async def algo_signals():
    """
    Algo tab: compute STRONG BUY / STRONG SELL signals for NIFTY & SENSEX.
    Returns the recommended CE/PE option strike, buy/sell price, target, SL.
    """
    from analysis.technical import (
        calculate_ema, calculate_rsi, calculate_macd,
        calculate_supertrend, calculate_atr, calculate_bollinger_bands,
    )

    results = {}

    for index in ["NIFTY", "SENSEX"]:
        try:
            df = _get_historical_df(index, "15minute", 5)
            if df.empty or len(df) < 50:
                results[index] = {"signal": "NO DATA"}
                continue

            close = df["close"]
            high  = df["high"]
            low   = df["low"]
            spot  = float(close.iloc[-1])

            # ── Indicators ──────────────────────────────────────
            ema9   = calculate_ema(close, 9)
            ema21  = calculate_ema(close, 21)
            ema50  = calculate_ema(close, 50)
            ema200 = calculate_ema(close, 200)
            rsi    = calculate_rsi(close, 14)
            macd_d = calculate_macd(close)
            hist   = macd_d["histogram"]
            macd_l = macd_d["macd"]
            sig_l  = macd_d["signal"]
            atr    = calculate_atr(high, low, close, 14)
            bb     = calculate_bollinger_bands(close, 20, 2)

            try:
                st_data = calculate_supertrend(high, low, close, 10, 3)
                st_dir  = st_data.get("direction", pd.Series([1]*len(df), index=df.index))
            except Exception:
                st_dir = pd.Series([1]*len(df), index=df.index)

            i = len(df) - 1
            atr_val   = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else spot * 0.005
            rsi_val   = float(rsi.iloc[i])
            e9        = float(ema9.iloc[i])
            e21       = float(ema21.iloc[i])
            e50       = float(ema50.iloc[i])
            e200      = float(ema200.iloc[i]) if len(ema200) > i else spot
            hist_now  = float(hist.iloc[i])
            hist_prev = float(hist.iloc[i - 1])
            macd_now  = float(macd_l.iloc[i])
            sig_now   = float(sig_l.iloc[i])
            st_now    = int(st_dir.iloc[i])
            bb_upper  = float(bb["upper"].iloc[i])
            bb_lower  = float(bb["lower"].iloc[i])
            bb_mid    = float(bb["middle"].iloc[i])

            # ── Score system ─────────────────────────────────────
            score_buy = score_sell = 0
            reasons_buy = []
            reasons_sell = []

            # 1. EMA alignment (trend)
            if e9 > e21 > e50:
                score_buy += 2; reasons_buy.append("EMA9 > EMA21 > EMA50 (bullish alignment)")
            if e9 < e21 < e50:
                score_sell += 2; reasons_sell.append("EMA9 < EMA21 < EMA50 (bearish alignment)")

            # 2. Price vs EMA200
            if spot > e200:
                score_buy += 1; reasons_buy.append("Price above EMA200 (long-term uptrend)")
            else:
                score_sell += 1; reasons_sell.append("Price below EMA200 (long-term downtrend)")

            # 3. MACD above signal
            if macd_now > sig_now and hist_now > 0:
                score_buy += 2; reasons_buy.append("MACD above signal & positive histogram")
            if macd_now < sig_now and hist_now < 0:
                score_sell += 2; reasons_sell.append("MACD below signal & negative histogram")

            # 4. MACD histogram momentum
            if hist_prev < 0 and hist_now > 0:
                score_buy += 2; reasons_buy.append("MACD histogram flipped positive")
            if hist_prev > 0 and hist_now < 0:
                score_sell += 2; reasons_sell.append("MACD histogram flipped negative")

            # 5. RSI
            if 50 < rsi_val < 70:
                score_buy += 2; reasons_buy.append(f"RSI {rsi_val:.1f} in bullish zone (50-70)")
            if 30 < rsi_val < 50:
                score_sell += 2; reasons_sell.append(f"RSI {rsi_val:.1f} in bearish zone (30-50)")
            if rsi_val >= 70:
                score_sell += 1; reasons_sell.append(f"RSI {rsi_val:.1f} overbought — potential reversal")
            if rsi_val <= 30:
                score_buy += 1; reasons_buy.append(f"RSI {rsi_val:.1f} oversold — potential bounce")

            # 6. Supertrend
            if st_now == 1:
                score_buy += 2; reasons_buy.append("Supertrend bullish (price above band)")
            else:
                score_sell += 2; reasons_sell.append("Supertrend bearish (price below band)")

            # 7. Bollinger Band position
            if spot > bb_mid and spot < bb_upper:
                score_buy += 1; reasons_buy.append("Price above BB midline, room to upper band")
            if spot < bb_mid and spot > bb_lower:
                score_sell += 1; reasons_sell.append("Price below BB midline, room to lower band")
            if spot >= bb_upper:
                score_sell += 1; reasons_sell.append("Price at BB upper band — overbought stretch")
            if spot <= bb_lower:
                score_buy += 1; reasons_buy.append("Price at BB lower band — oversold bounce likely")

            # ── Determine signal ──────────────────────────────────
            total = score_buy + score_sell
            confidence = round((max(score_buy, score_sell) / max(total, 1)) * 100)
            buy_pct  = round(score_buy  / max(total, 1) * 100)
            sell_pct = round(score_sell / max(total, 1) * 100)

            lot_size = LOT_SIZES.get(index.upper(), 25)
            strike_gap = 50 if index == "NIFTY" else 100
            exchange = "NFO" if index == "NIFTY" else "BFO"
            expiry_dt = get_nearest_expiry(index)

            if score_buy >= 8 and score_buy > score_sell + 2:
                direction = "STRONG BUY"
                color = "green"
                reasons = reasons_buy
                # CE recommendation (buy call)
                ce_strike = round(spot / strike_gap) * strike_gap
                ce_premium = round(spot * 0.008 + atr_val * 0.5, 1)
                ce_target  = round(ce_premium * 1.6, 1)
                ce_sl      = round(ce_premium * 0.55, 1)
                pe_note    = "SELL PE (short put for premium)"
                pe_strike  = ce_strike - strike_gap * 2
                pe_premium = round(spot * 0.005 + atr_val * 0.3, 1)
                pe_target  = round(pe_premium * 0.3, 1)   # target decay to 30%
                pe_sl      = round(pe_premium * 1.5, 1)

            elif score_sell >= 8 and score_sell > score_buy + 2:
                direction = "STRONG SELL"
                color = "red"
                reasons = reasons_sell
                # PE recommendation (buy put)
                pe_strike  = round(spot / strike_gap) * strike_gap
                pe_premium = round(spot * 0.008 + atr_val * 0.5, 1)
                pe_target  = round(pe_premium * 1.6, 1)
                pe_sl      = round(pe_premium * 0.55, 1)
                ce_note    = "SELL CE (short call for premium)"
                ce_strike  = pe_strike + strike_gap * 2
                ce_premium = round(spot * 0.005 + atr_val * 0.3, 1)
                ce_target  = round(ce_premium * 0.3, 1)
                ce_sl      = round(ce_premium * 1.5, 1)

            elif score_buy > score_sell:
                direction = "MILD BUY"
                color = "blue"
                reasons = reasons_buy
                ce_strike  = round(spot / strike_gap) * strike_gap
                ce_premium = round(spot * 0.007 + atr_val * 0.4, 1)
                ce_target  = round(ce_premium * 1.4, 1)
                ce_sl      = round(ce_premium * 0.6, 1)
                pe_strike  = ce_strike - strike_gap
                pe_premium = round(spot * 0.004, 1)
                pe_target  = round(pe_premium * 0.35, 1)
                pe_sl      = round(pe_premium * 1.4, 1)

            elif score_sell > score_buy:
                direction = "MILD SELL"
                color = "orange"
                reasons = reasons_sell
                pe_strike  = round(spot / strike_gap) * strike_gap
                pe_premium = round(spot * 0.007 + atr_val * 0.4, 1)
                pe_target  = round(pe_premium * 1.4, 1)
                pe_sl      = round(pe_premium * 0.6, 1)
                ce_strike  = pe_strike + strike_gap
                ce_premium = round(spot * 0.004, 1)
                ce_target  = round(ce_premium * 0.35, 1)
                ce_sl      = round(ce_premium * 1.4, 1)

            else:
                direction = "NEUTRAL"
                color = "yellow"
                reasons = ["Indicators are mixed — wait for clearer setup"]
                ce_strike  = round(spot / strike_gap) * strike_gap
                pe_strike  = ce_strike
                ce_premium = pe_premium = round(spot * 0.006, 1)
                ce_target  = pe_target = round(ce_premium * 1.3, 1)
                ce_sl      = pe_sl = round(ce_premium * 0.65, 1)

            results[index] = sanitize({
                "index": index,
                "spot": spot,
                "signal": direction,
                "color": color,
                "score_buy": score_buy,
                "score_sell": score_sell,
                "confidence": confidence,
                "buy_pct": buy_pct,
                "sell_pct": sell_pct,
                "rsi": round(rsi_val, 1),
                "atr": round(atr_val, 1),
                "lot_size": lot_size,
                "reasons": reasons[:6],
                "ce": {
                    "strike": ce_strike,
                    "action": "BUY CE" if direction in ("STRONG BUY","MILD BUY") else "SELL CE",
                    "txn": "BUY" if direction in ("STRONG BUY","MILD BUY") else "SELL",
                    "entry": ce_premium,
                    "target": ce_target,
                    "sl": ce_sl,
                    "reward_risk": round((ce_target - ce_premium) / max(ce_premium - ce_sl, 0.1), 2),
                    "tradingsymbol": _build_nfo_symbol(index, expiry_dt, ce_strike, "CE"),
                    "exchange": exchange,
                    "lot_size": lot_size,
                    "expiry": expiry_dt.strftime("%d %b %Y"),
                },
                "pe": {
                    "strike": pe_strike,
                    "action": "BUY PE" if direction in ("STRONG SELL","MILD SELL") else "SELL PE",
                    "txn": "BUY" if direction in ("STRONG SELL","MILD SELL") else "SELL",
                    "entry": pe_premium,
                    "target": pe_target,
                    "sl": pe_sl,
                    "reward_risk": round(abs(pe_target - pe_premium) / max(abs(pe_premium - pe_sl), 0.1), 2),
                    "tradingsymbol": _build_nfo_symbol(index, expiry_dt, pe_strike, "PE"),
                    "exchange": exchange,
                    "lot_size": lot_size,
                    "expiry": expiry_dt.strftime("%d %b %Y"),
                },
                "timestamp": datetime.now().isoformat(),
            })

        except Exception as ex:
            results[index] = {"signal": "ERROR", "error": str(ex)}

    return JSONResponse(results)


# ──────────────────────────────────────────────────────────────────────────────
# ADVANCED ANALYTICS ENDPOINTS
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/gex/{index}")
async def gamma_exposure(index: str):
    """Gamma Exposure (GEX) per strike — shows dealer hedging walls."""
    from analysis.options import enrich_option_chain_with_greeks, calculate_max_pain

    lot_size = LOT_SIZES.get(index.upper(), 25)
    expiry   = get_nearest_expiry(index).strftime("%Y-%m-%d")
    chain, spot = _get_option_chain_list(index, expiry)

    expiry_days = days_to_expiry(expiry)
    chain = enrich_option_chain_with_greeks(chain, spot, expiry_days, RISK_FREE_RATE)

    gex_data = []
    total_gex = 0
    for row in chain:
        strike = row.get("strike", 0)
        ce = row.get("CE") or {}
        pe = row.get("PE") or {}
        ce_gamma = (ce.get("greeks") or {}).get("gamma", 0) or 0
        pe_gamma = (pe.get("greeks") or {}).get("gamma", 0) or 0
        ce_oi    = ce.get("oi", 0) or 0
        pe_oi    = pe.get("oi", 0) or 0
        # GEX = gamma × OI × lot_size × spot² / 100 (standard formula)
        ce_gex   = ce_gamma * ce_oi * lot_size * spot * spot / 1e7
        pe_gex   = pe_gamma * pe_oi * lot_size * spot * spot / 1e7
        net_gex  = round(ce_gex - pe_gex, 2)
        total_gex += net_gex
        gex_data.append({
            "strike": strike,
            "ce_gex": round(ce_gex, 2),
            "pe_gex": round(pe_gex, 2),
            "net_gex": net_gex,
            "ce_gamma": round(ce_gamma, 6),
            "pe_gamma": round(pe_gamma, 6),
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
        })

    # Key GEX levels
    gex_data_sorted = sorted(gex_data, key=lambda x: abs(x["net_gex"]), reverse=True)
    flip_level = next((g["strike"] for g in gex_data if g["net_gex"] < 0), None)

    return JSONResponse(sanitize({
        "index": index.upper(),
        "spot": spot,
        "lot_size": lot_size,
        "total_gex": round(total_gex, 2),
        "gex_flip": flip_level,
        "regime": "POSITIVE GEX — Market stabilising" if total_gex > 0 else "NEGATIVE GEX — Market accelerating",
        "strikes": gex_data,
        "top_walls": gex_data_sorted[:5],
    }))


@app.get("/api/oi-heatmap/{index}")
async def oi_heatmap(index: str):
    """OI Change Heatmap — shows support/resistance walls from option writing."""
    expiry = get_nearest_expiry(index).strftime("%Y-%m-%d")
    chain, spot = _get_option_chain_list(index, expiry)

    rows = []
    max_ce_oi_chg = max_pe_oi_chg = 1
    for row in chain:
        ce = row.get("CE") or {}
        pe = row.get("PE") or {}
        ce_oi_chg = abs(ce.get("oi_change", 0) or 0)
        pe_oi_chg = abs(pe.get("oi_change", 0) or 0)
        if ce_oi_chg > max_ce_oi_chg: max_ce_oi_chg = ce_oi_chg
        if pe_oi_chg > max_pe_oi_chg: max_pe_oi_chg = pe_oi_chg
        rows.append({
            "strike":    row.get("strike", 0),
            "ce_oi":     ce.get("oi", 0) or 0,
            "pe_oi":     pe.get("oi", 0) or 0,
            "ce_oi_chg": ce.get("oi_change", 0) or 0,
            "pe_oi_chg": pe.get("oi_change", 0) or 0,
            "ce_ltp":    ce.get("ltp", 0) or 0,
            "pe_ltp":    pe.get("ltp", 0) or 0,
        })

    # Tag each strike: strong support (PE writing), resistance (CE writing)
    for r in rows:
        ce_pct = abs(r["ce_oi_chg"]) / max_ce_oi_chg * 100
        pe_pct = abs(r["pe_oi_chg"]) / max_pe_oi_chg * 100
        r["ce_heat"] = round(ce_pct, 1)
        r["pe_heat"] = round(pe_pct, 1)
        if pe_pct > 70 and r["pe_oi_chg"] > 0:
            r["tag"] = "STRONG SUPPORT"
        elif ce_pct > 70 and r["ce_oi_chg"] > 0:
            r["tag"] = "STRONG RESISTANCE"
        elif r["strike"] < spot and r["pe_oi_chg"] > 0:
            r["tag"] = "SUPPORT"
        elif r["strike"] > spot and r["ce_oi_chg"] > 0:
            r["tag"] = "RESISTANCE"
        else:
            r["tag"] = ""

    return JSONResponse(sanitize({"index": index.upper(), "spot": spot, "expiry": expiry, "strikes": rows}))


@app.get("/api/iv-surface/{index}")
async def iv_surface(index: str):
    """IV Skew, IV Smile, IVP (IV Percentile) across strikes."""
    from mock_data import get_mock_option_chain
    from analysis.options import enrich_option_chain_with_greeks, get_iv_rank

    expiry = get_nearest_expiry(index).strftime("%Y-%m-%d")
    chain, spot = _get_option_chain_list(index, expiry)
    expiry_days = days_to_expiry(expiry)
    chain = enrich_option_chain_with_greeks(chain, spot, expiry_days, RISK_FREE_RATE)

    iv_rows = []
    atm_iv_ce = atm_iv_pe = 0
    atm_strike = get_atm_strike(spot, index)

    for row in chain:
        strike = row.get("strike", 0)
        ce = row.get("CE") or {}
        pe = row.get("PE") or {}
        ce_iv = ce.get("iv", 0) or 0
        pe_iv = pe.get("iv", 0) or 0
        moneyness = round((strike - spot) / spot * 100, 2)
        iv_rows.append({
            "strike": strike,
            "moneyness": moneyness,
            "ce_iv": ce_iv,
            "pe_iv": pe_iv,
            "skew": round(pe_iv - ce_iv, 2),
        })
        if strike == atm_strike:
            atm_iv_ce = ce_iv
            atm_iv_pe = pe_iv

    atm_iv = (atm_iv_ce + atm_iv_pe) / 2 if (atm_iv_ce + atm_iv_pe) > 0 else 20
    # Simulate IVP from mock historical (real: compare vs 52-week IV range)
    iv_52w_low  = atm_iv * 0.6
    iv_52w_high = atm_iv * 1.8
    ivp = round((atm_iv - iv_52w_low) / max(iv_52w_high - iv_52w_low, 1) * 100, 1)

    skew_direction = "PUT SKEW" if atm_iv_pe > atm_iv_ce else "CALL SKEW"
    iv_regime = "HIGH IV — Sell options" if ivp > 60 else ("LOW IV — Buy options" if ivp < 40 else "NORMAL IV")

    return JSONResponse(sanitize({
        "index": index.upper(),
        "spot": spot,
        "expiry": expiry,
        "expiry_days": expiry_days,
        "atm_iv": round(atm_iv, 2),
        "atm_iv_ce": atm_iv_ce,
        "atm_iv_pe": atm_iv_pe,
        "iv_percentile": ivp,
        "iv_52w_low": round(iv_52w_low, 2),
        "iv_52w_high": round(iv_52w_high, 2),
        "skew_direction": skew_direction,
        "iv_regime": iv_regime,
        "strikes": iv_rows,
    }))


@app.get("/api/mtf-signals/{index}")
async def mtf_signals(index: str):
    """Multi-Timeframe signal alignment — 5min, 15min, 1hr, Daily."""
    from analysis.technical import (
        calculate_ema, calculate_rsi, calculate_macd, calculate_supertrend, calculate_atr,
    )

    timeframes = [
        ("5minute",  3,  "5 Min"),
        ("15minute", 5,  "15 Min"),
        ("60minute", 20, "1 Hour"),
        ("day",      60, "Daily"),
    ]
    results = []

    for interval, days, label in timeframes:
        try:
            df = _get_historical_df(index, interval, days)
            if df.empty or len(df) < 20:
                results.append({"tf": label, "bias": "NO DATA", "score": 0, "rsi": 0, "details": []})
                continue

            close = df["close"]
            high  = df["high"]
            low   = df["low"]
            ema9  = calculate_ema(close, 9)
            ema21 = calculate_ema(close, 21)
            ema50 = calculate_ema(close, 50)
            rsi   = calculate_rsi(close, 14)
            macd_d = calculate_macd(close)
            hist   = macd_d["histogram"]
            try:
                st = calculate_supertrend(high, low, close, 10, 3)
                st_dir = st.get("direction", pd.Series([1]*len(df), index=df.index))
            except Exception:
                st_dir = pd.Series([1]*len(df), index=df.index)

            i = len(df) - 1
            score = 0
            details = []

            e9, e21, e50 = float(ema9.iloc[i]), float(ema21.iloc[i]), float(ema50.iloc[i])
            rsi_v = float(rsi.iloc[i])
            hist_v = float(hist.iloc[i])
            st_v  = int(st_dir.iloc[i])
            price = float(close.iloc[i])

            if e9 > e21 > e50:   score += 2; details.append("EMA bullish ▲")
            elif e9 < e21 < e50: score -= 2; details.append("EMA bearish ▼")

            if hist_v > 0:   score += 1; details.append("MACD+ ▲")
            else:            score -= 1; details.append("MACD− ▼")

            if rsi_v > 55:   score += 1; details.append(f"RSI {rsi_v:.0f} ▲")
            elif rsi_v < 45: score -= 1; details.append(f"RSI {rsi_v:.0f} ▼")
            else:            details.append(f"RSI {rsi_v:.0f} →")

            if st_v == 1:    score += 1; details.append("ST Bullish ▲")
            else:            score -= 1; details.append("ST Bearish ▼")

            if price > e50:  score += 1; details.append("Above EMA50 ▲")
            else:            score -= 1; details.append("Below EMA50 ▼")

            bias = "BULLISH" if score >= 3 else ("BEARISH" if score <= -3 else "NEUTRAL")
            results.append({
                "tf": label, "bias": bias, "score": score,
                "rsi": round(rsi_v, 1), "details": details,
                "price": round(price, 2),
            })
        except Exception as ex:
            results.append({"tf": label, "bias": "ERROR", "score": 0, "rsi": 0, "details": [str(ex)]})

    # Overall alignment
    scores = [r["score"] for r in results if r["bias"] not in ("NO DATA","ERROR")]
    total  = sum(scores)
    aligned_bull = all(r["score"] > 0 for r in results if r["bias"] not in ("NO DATA","ERROR"))
    aligned_bear = all(r["score"] < 0 for r in results if r["bias"] not in ("NO DATA","ERROR"))
    if aligned_bull:  overall = "STRONG BUY — All TFs aligned bullish"
    elif aligned_bear: overall = "STRONG SELL — All TFs aligned bearish"
    elif total > 4:   overall = "MILD BUY — Majority bullish"
    elif total < -4:  overall = "MILD SELL — Majority bearish"
    else:             overall = "MIXED — Wait for alignment"

    return JSONResponse(sanitize({
        "index": index.upper(),
        "overall": overall,
        "total_score": total,
        "timeframes": results,
    }))


@app.get("/api/strategy-payoff/{index}")
async def strategy_payoff(index: str, strategy: str = "iron_condor"):
    """Option strategy P&L payoff data for chart rendering."""
    lot_size = LOT_SIZES.get(index.upper(), 25)
    expiry   = get_nearest_expiry(index).strftime("%Y-%m-%d")
    chain, spot = _get_option_chain_list(index, expiry)

    atm = get_atm_strike(spot, index)
    gap = 50 if index.upper() == "NIFTY" else 100

    # Build strategy legs based on option chain premiums
    chain_dict = {}
    for row in chain:
        s = row.get("strike", 0)
        chain_dict[s] = row

    def get_ltp(strike, opt_type):
        row = chain_dict.get(strike, {})
        return (row.get(opt_type) or {}).get("ltp", 0) or 0

    strategies = {
        "iron_condor": {
            "name": "Iron Condor",
            "description": "Sell OTM Call + Put, Buy further OTM Call + Put. Profit if market stays range-bound.",
            "legs": [
                {"action":"SELL","type":"CE","strike":atm+gap*2,   "premium":get_ltp(atm+gap*2,"CE")},
                {"action":"SELL","type":"PE","strike":atm-gap*2,   "premium":get_ltp(atm-gap*2,"PE")},
                {"action":"BUY", "type":"CE","strike":atm+gap*4,   "premium":get_ltp(atm+gap*4,"CE")},
                {"action":"BUY", "type":"PE","strike":atm-gap*4,   "premium":get_ltp(atm-gap*4,"PE")},
            ],
        },
        "straddle": {
            "name": "Short Straddle",
            "description": "Sell ATM Call + Put. Profit from time decay if market stays near ATM.",
            "legs": [
                {"action":"SELL","type":"CE","strike":atm,"premium":get_ltp(atm,"CE")},
                {"action":"SELL","type":"PE","strike":atm,"premium":get_ltp(atm,"PE")},
            ],
        },
        "strangle": {
            "name": "Short Strangle",
            "description": "Sell OTM Call + Put. Wider range than straddle, lower premium collected.",
            "legs": [
                {"action":"SELL","type":"CE","strike":atm+gap*2,"premium":get_ltp(atm+gap*2,"CE")},
                {"action":"SELL","type":"PE","strike":atm-gap*2,"premium":get_ltp(atm-gap*2,"PE")},
            ],
        },
        "bull_call_spread": {
            "name": "Bull Call Spread",
            "description": "Buy ATM CE, Sell OTM CE. Limited profit, limited loss — bullish strategy.",
            "legs": [
                {"action":"BUY", "type":"CE","strike":atm,      "premium":get_ltp(atm,"CE")},
                {"action":"SELL","type":"CE","strike":atm+gap*2,"premium":get_ltp(atm+gap*2,"CE")},
            ],
        },
        "bear_put_spread": {
            "name": "Bear Put Spread",
            "description": "Buy ATM PE, Sell OTM PE. Limited profit, limited loss — bearish strategy.",
            "legs": [
                {"action":"BUY", "type":"PE","strike":atm,      "premium":get_ltp(atm,"PE")},
                {"action":"SELL","type":"PE","strike":atm-gap*2,"premium":get_ltp(atm-gap*2,"PE")},
            ],
        },
        "butterfly": {
            "name": "Long Butterfly",
            "description": "Buy 1 ITM CE, Sell 2 ATM CE, Buy 1 OTM CE. Very low cost, profit at ATM on expiry.",
            "legs": [
                {"action":"BUY", "type":"CE","strike":atm-gap*2,"premium":get_ltp(atm-gap*2,"CE")},
                {"action":"SELL","type":"CE","strike":atm,      "premium":get_ltp(atm,"CE"),"qty":2},
                {"action":"BUY", "type":"CE","strike":atm+gap*2,"premium":get_ltp(atm+gap*2,"CE")},
            ],
        },
    }

    strat = strategies.get(strategy, strategies["iron_condor"])
    legs  = strat["legs"]

    # Compute payoff at expiry across price range
    price_range = [spot * (1 + i * 0.002) for i in range(-50, 51)]
    payoff_points = []
    net_premium = 0
    for leg in legs:
        qty = leg.get("qty", 1)
        mult = -1 if leg["action"] == "SELL" else 1
        if leg["type"] == "CE":
            net_premium += -mult * leg["premium"] * qty
        else:
            net_premium += -mult * leg["premium"] * qty

    for price in price_range:
        pnl = 0
        for leg in legs:
            qty  = leg.get("qty", 1)
            mult = -1 if leg["action"] == "SELL" else 1
            prem = leg["premium"]
            k    = leg["strike"]
            if leg["type"] == "CE":
                intrinsic = max(price - k, 0)
            else:
                intrinsic = max(k - price, 0)
            pnl += mult * (intrinsic - prem) * qty
        payoff_points.append({"price": round(price, 0), "pnl": round(pnl * lot_size, 0)})

    # Stats
    pnls    = [p["pnl"] for p in payoff_points]
    max_profit = max(pnls)
    max_loss   = min(pnls)
    breakevens = []
    for j in range(1, len(payoff_points)):
        if (payoff_points[j-1]["pnl"] < 0) != (payoff_points[j]["pnl"] < 0):
            breakevens.append(round((payoff_points[j-1]["price"]+payoff_points[j]["price"])/2))

    return JSONResponse(sanitize({
        "index": index.upper(),
        "strategy": strategy,
        "name": strat["name"],
        "description": strat["description"],
        "spot": spot,
        "atm": atm,
        "lot_size": lot_size,
        "legs": legs,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakevens": breakevens,
        "net_premium": round(net_premium * lot_size, 0),
        "payoff": payoff_points,
    }))


@app.get("/api/economic-calendar")
async def economic_calendar():
    """Upcoming market events, expiry dates, and high-impact news."""
    from datetime import timedelta
    today = date.today()

    events = []

    # F&O Expiry dates (next 4 weeks)
    for i in range(28):
        d = today + timedelta(days=i)
        if d.weekday() == 3:  # Thursday = NIFTY expiry
            events.append({"date": d.isoformat(), "event": "NIFTY Weekly Expiry", "type": "expiry", "impact": "HIGH", "index": "NIFTY"})
        if d.weekday() == 4:  # Friday = SENSEX expiry
            events.append({"date": d.isoformat(), "event": "SENSEX Weekly Expiry", "type": "expiry", "impact": "HIGH", "index": "SENSEX"})

    # Known scheduled events (static — update quarterly)
    scheduled = [
        {"date": "2026-06-06", "event": "India CPI Inflation Data", "type": "macro",  "impact": "HIGH",   "index": "ALL"},
        {"date": "2026-06-07", "event": "India IIP Industrial Output", "type": "macro","impact": "MEDIUM", "index": "ALL"},
        {"date": "2026-06-10", "event": "US CPI Inflation", "type": "global",          "impact": "HIGH",   "index": "ALL"},
        {"date": "2026-06-11", "event": "RBI MPC Meeting Begins", "type": "rbi",        "impact": "HIGH",   "index": "ALL"},
        {"date": "2026-06-13", "event": "RBI Policy Decision", "type": "rbi",           "impact": "HIGH",   "index": "ALL"},
        {"date": "2026-06-18", "event": "US FOMC Meeting Decision", "type": "global",   "impact": "HIGH",   "index": "ALL"},
        {"date": "2026-06-25", "event": "India GDP Q4 Advance Estimate", "type": "macro","impact":"HIGH",   "index": "ALL"},
        {"date": "2026-07-01", "event": "GST Council Meeting", "type": "macro",         "impact": "MEDIUM", "index": "ALL"},
        {"date": "2026-07-22", "event": "Union Budget 2026-27", "type": "macro",        "impact": "EXTREME","index": "ALL"},
    ]

    for e in scheduled:
        try:
            edate = date.fromisoformat(e["date"])
            delta = (edate - today).days
            if -2 <= delta <= 30:
                e["days_away"] = delta
                e["label"] = "TODAY" if delta == 0 else (f"in {delta}d" if delta > 0 else f"{-delta}d ago")
                events.append(e)
        except Exception:
            pass

    events.sort(key=lambda x: x["date"])
    return JSONResponse({"events": events, "today": today.isoformat()})


@app.get("/api/backtest/{index}")
async def backtest_signals(index: str, days: int = 30):
    """Backtest the algo signal on historical data — win rate, avg P&L."""
    from analysis.technical import (
        calculate_ema, calculate_rsi, calculate_macd, calculate_supertrend,
        calculate_atr, calculate_bollinger_bands,
    )

    df = _get_historical_df(index, "day", max(days + 10, 60))
    if df.empty or len(df) < 30:
        return JSONResponse({"error": "Insufficient data"})

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    ema9   = calculate_ema(close, 9)
    ema21  = calculate_ema(close, 21)
    ema50  = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    rsi    = calculate_rsi(close, 14)
    macd_d = calculate_macd(close)
    hist   = macd_d["histogram"]
    macd_l = macd_d["macd"]
    sig_l  = macd_d["signal"]
    atr    = calculate_atr(high, low, close, 14)
    bb     = calculate_bollinger_bands(close, 20, 2)
    try:
        st_data = calculate_supertrend(high, low, close, 10, 3)
        st_dir  = st_data.get("direction", pd.Series([1]*len(df), index=df.index))
    except Exception:
        st_dir  = pd.Series([1]*len(df), index=df.index)

    trades = []
    df_r   = df.reset_index()

    for i in range(10, len(df_r) - 1):
        c  = float(close.iloc[i])
        e9 = float(ema9.iloc[i])
        e21= float(ema21.iloc[i])
        e50= float(ema50.iloc[i])
        e200=float(ema200.iloc[i])
        r  = float(rsi.iloc[i])
        h  = float(hist.iloc[i])
        m  = float(macd_l.iloc[i])
        s  = float(sig_l.iloc[i])
        st = int(st_dir.iloc[i])
        bbu= float(bb["upper"].iloc[i])
        bbm= float(bb["middle"].iloc[i])
        bbl= float(bb["lower"].iloc[i])
        a  = float(atr.iloc[i])

        score_b = score_s = 0
        if e9>e21>e50:    score_b += 2
        if e9<e21<e50:    score_s += 2
        if c > e200:      score_b += 1
        else:             score_s += 1
        if m > s and h > 0: score_b += 2
        if m < s and h < 0: score_s += 2
        if 50 < r < 70:   score_b += 2
        if 30 < r < 50:   score_s += 2
        if st == 1:       score_b += 2
        else:             score_s += 2
        if c > bbm:       score_b += 1
        else:             score_s += 1

        direction = None
        if score_b >= 8 and score_b > score_s + 2: direction = "BUY"
        elif score_s >= 8 and score_s > score_b + 2: direction = "SELL"

        if direction:
            entry = c
            next_close = float(close.iloc[i + 1])
            sl_dist    = a * 1.5
            tgt_dist   = a * 2.5

            if direction == "BUY":
                sl     = entry - sl_dist
                target = entry + tgt_dist
                actual = next_close
                hit_target = actual >= target
                hit_sl     = actual <= sl
                if hit_target: outcome = "WIN"; pnl = tgt_dist
                elif hit_sl:   outcome = "LOSS"; pnl = -sl_dist
                else:          outcome = "OPEN"; pnl = actual - entry
            else:
                sl     = entry + sl_dist
                target = entry - tgt_dist
                actual = next_close
                hit_target = actual <= target
                hit_sl     = actual >= sl
                if hit_target: outcome = "WIN";  pnl = tgt_dist
                elif hit_sl:   outcome = "LOSS"; pnl = -sl_dist
                else:          outcome = "OPEN"; pnl = entry - actual

            trades.append({
                "date":      df_r.iloc[i]["date"].strftime("%Y-%m-%d"),
                "direction": direction,
                "entry":     round(entry, 2),
                "sl":        round(sl, 2),
                "target":    round(target, 2),
                "actual":    round(actual, 2),
                "outcome":   outcome,
                "pnl_pts":   round(pnl, 2),
                "score_b":   score_b,
                "score_s":   score_s,
            })

    wins  = [t for t in trades if t["outcome"] == "WIN"]
    losses= [t for t in trades if t["outcome"] == "LOSS"]
    total_pnl = sum(t["pnl_pts"] for t in trades)
    win_rate  = round(len(wins) / max(len(trades), 1) * 100, 1)
    avg_win   = round(sum(t["pnl_pts"] for t in wins) / max(len(wins), 1), 2)
    avg_loss  = round(sum(t["pnl_pts"] for t in losses) / max(len(losses), 1), 2)
    profit_factor = round(abs(sum(t["pnl_pts"] for t in wins)) / max(abs(sum(t["pnl_pts"] for t in losses)), 1), 2)

    return JSONResponse(sanitize({
        "index":         index.upper(),
        "days_tested":   days,
        "total_trades":  len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      win_rate,
        "total_pnl_pts": round(total_pnl, 2),
        "avg_win_pts":   avg_win,
        "avg_loss_pts":  avg_loss,
        "profit_factor": profit_factor,
        "trades":        trades[-days:],
    }))


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
