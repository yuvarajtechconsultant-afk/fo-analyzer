# F&O Analyzer — NIFTY & SENSEX

A professional, real-time Futures & Options analysis dashboard for Indian markets, powered by the Zerodha KiteConnect API.

## Features

- **Live price streaming** via WebSocket for NIFTY, SENSEX, and India VIX
- **Full Option Chain** with real-time Greeks (Delta, Gamma, Theta, Vega) computed via Black-Scholes
- **PCR, Max Pain, OI Buildup** analysis for directional bias
- **Technical Analysis**: RSI, MACD, Bollinger Bands, Supertrend, EMA/SMA, VWAP, Stochastic, ATR
- **Pivot Points**: Classic, Fibonacci, and Camarilla
- **Composite Market Sentiment** gauge combining PCR, VIX, FII/DII, and Advance/Decline
- **Trade Signal Generator** with entry, target, stop-loss, and risk/reward
- **Strategy Suggestions**: Bull Call Spread, Bear Put Spread, Iron Condor, Straddle, and more
- **Order Placement** modal with direct Zerodha integration
- **Dark-themed professional UI** with flash animations on price updates

---

## Setup

### 1. Clone / download and install requirements

```bash
cd D:\Sensex-Nifty-FutureOptions
pip install -r requirements.txt
```

### 2. Create a Zerodha Developer Account

1. Go to [https://developers.kite.trade/](https://developers.kite.trade/) and sign in with your Zerodha account.
2. Create a new app — set the **Redirect URL** to `http://127.0.0.1:8000/` (or whatever port you use).
3. Note your **API Key** and **API Secret**.

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
copy .env.example .env
```

Edit `.env`:

```
KITE_API_KEY=your_api_key_here
KITE_API_SECRET=your_api_secret_here
KITE_ACCESS_TOKEN=        # leave blank; generated at login
```

> **Tip**: The `KITE_ACCESS_TOKEN` can optionally be pre-filled if you already have a valid token (tokens expire daily at 6 AM).

### 4. Run the server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Login Flow

1. Click the **Login** button in the top-right navbar.
2. A Zerodha login popup opens — complete 2FA there.
3. Zerodha redirects back to your app's redirect URL with a `request_token` query parameter.
4. The app automatically exchanges the token and stores the `access_token` in memory.
5. All subsequent API calls use the live token. The navbar login button turns green ("Connected").

> Access tokens expire at 6 AM IST the following day. You need to log in once per trading day.

---

## Usage Guide

### Dashboard
- Auto-refreshes every 5 seconds via polling + WebSocket for prices.
- Shows composite sentiment gauge, NIFTY/SENSEX signal cards, PCR, Max Pain, Expected Move, VIX, FII/DII table, and Advance/Decline breadth.

### Option Chain
- Select index (NIFTY/SENSEX) and expiry date.
- ATM strike is highlighted in yellow.
- Green shading = heavy CE OI buildup; Red = heavy PE OI.
- OI bars show relative open interest visually.

### Technical Analysis
- Select index and time interval (5m, 15m, 30m, 1h, 1D).
- TradingView lightweight-charts candlestick chart renders live.
- Indicator cards below: RSI gauge, MACD, Bollinger, Supertrend, EMAs, VWAP, Stoch, ATR.
- Pivot table shows Classic, Fibonacci, and Camarilla levels.

### Signals
- Full signal with entry/target/SL/R:R computed from combined technical + options + sentiment.
- Suggested strategies listed with legs, max profit/loss, and confidence.
- "Place Order" button opens confirmation modal.

### Positions
- Requires Zerodha login.
- Shows open net positions with MTM P&L and today's orders.

---

## Architecture

```
main.py               FastAPI app, routes, WebSocket
config.py             Constants, expiry helpers
zerodha_client.py     KiteConnect wrapper (auth, quotes, chain, orders)
analysis/
  technical.py        RSI, MACD, BB, Supertrend, VWAP, pivots, S/R
  options.py          Black-Scholes greeks, IV, PCR, max pain, strategies
  market_breadth.py   FII/DII, A/D ratio, OI trend, composite sentiment
  signals.py          Master signal generator combining all inputs
templates/
  index.html          Single-page dark-theme dashboard
static/
  style.css           Extra CSS (print, transitions, tooltips)
```

---

## Notes

- **Mock data** is used automatically when not authenticated — the app is fully functional for demo/testing without a Zerodha account.
- All Greeks are computed locally using Black-Scholes (no external call needed for greeks).
- IV is solved using Brent's method via `scipy.optimize.brentq`.
- The app does **not** store credentials anywhere other than `.env` (which should never be committed).

---

## Disclaimer

This tool is for educational and informational purposes only. It does not constitute financial advice. Options trading involves significant risk of loss. Always do your own research and consult a SEBI-registered advisor before trading.
