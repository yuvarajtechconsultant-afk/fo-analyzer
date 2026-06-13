# F&O Analyzer — Complete Implementation Documentation
*Covers every implemented feature, analysis rule, formula, and endpoint.*
*Last updated: 12 Jun 2026*

---

## 1. Architecture Overview

| Layer | Technology |
|---|---|
| Backend | Python · FastAPI · `kiteconnect` (Zerodha) · pandas/numpy |
| Frontend | Single-page app — [templates/index.html](templates/index.html) (vanilla JS, LightweightCharts v4, Chart.js v4) |
| Market data | Zerodha KiteConnect when logged in; deterministic simulated data otherwise (`mock_data.py`) |
| Live updates | WebSocket `/ws/live` pushes NIFTY/SENSEX/VIX quotes every 2 s |
| Deployment | `start_app.ps1` — uvicorn on port **3000** + ngrok tunnel `https://herbs-idiocy-constable.ngrok-free.dev` (registered in Task Scheduler) |
| Dev preview | `.claude/launch.json` — uvicorn on port 8000 with `--reload` |

**Mock-data design:** every endpoint falls back to simulated data when not authenticated. Mock quotes are deterministic within 30-second windows and mock candles are deterministic per day/index/interval, so all views agree with each other and signals don't flap between refreshes.

**Key files**

```
main.py                  — all API endpoints + signal engines
zerodha_client.py        — KiteConnect wrapper (auth, quotes, chain, orders, logout)
config.py                — tokens, lot sizes, strike intervals, expiry rules
mock_data.py             — deterministic simulated quotes/candles/chains/FII-DII
analysis/technical.py    — SMA/EMA/RSI/MACD/BB/ATR/Supertrend/Pivots/VWAP/Stochastic
analysis/options.py      — Greeks, IV, PCR, Max Pain, OI buildup, expected move
analysis/market_breadth.py — FII/DII, advance-decline, composite sentiment
analysis/signals.py      — trade-signal generation/formatting
templates/index.html     — the entire UI (11 menu tabs)
```

---

## 2. Authentication

- **Login:** `GET /api/auth/login` returns the Kite OAuth URL → callback `GET /api/auth/callback?request_token=…` exchanges it for an access token (`generate_session`).
- **Logout:** `POST /api/auth/logout` calls Kite `invalidate_access_token()` (kills the session server-side at Zerodha) and clears the local token. Red **Logout** button appears in the navbar only while connected; confirmation dialog warns that data switches to mock.
- **Status:** `GET /api/auth/status` → `{authenticated: bool}`. Navbar shows blue **Login** → green **Connected**.

---

## 3. Expiry Rules (config.py)

Updated to post-Sep-2025 exchange rules:

| Index | Weekly expiry | Monthly expiry |
|---|---|---|
| NIFTY / BANKNIFTY / FINNIFTY | **Tuesday** | Last Tuesday |
| SENSEX | **Thursday** | Last Thursday |

When authenticated, the Algo additionally reads **real expiry dates from the Zerodha instrument list**, so it stays correct even if exchanges change rules again.

Lot sizes (`LOT_SIZES`): NIFTY 65, SENSEX 20, BANKNIFTY 15, FINNIFTY 40. Strike intervals: NIFTY 50, SENSEX 100.

---

## 4. Menu-by-Menu Implementation

### 4.1 📊 Dashboard
- **Market Sentiment gauge** — composite score from 5 components (see §5.6). Needle moves continuously with the score; explanation text under the gauge ("Combines PCR, VIX, market breadth & FII flows · Left = bearish · Right = bullish").
- **Sentiment Drivers table** — per-factor value/signal/score with a decoding footer (PCR > 1 = put writers active, etc.). The component scores sum exactly to the gauge score.
- **Signal cards (NIFTY & SENSEX)** — entry/target/SL/R:R/expiry/bias plus an **"In plain words"** sentence (e.g. "BUY NIFTY 22500 CE at 180 — book profit at 288, exit if it falls to 126") and top-3 reasons.
- **Metric cards** with meaning-rich subtitles: PCR ("↑ Bullish — put writing builds support"), Max Pain ("price gravitates here near expiry"), Expected Move (range till expiry), India VIX ("Low fear — option premiums cheap").
- **FII/DII + Advance/Decline** — currently simulated data (see roadmap §8).

### 4.2 ⛓️ Option Chain
Full chain with LTP/OI/OI-change/volume/bid-ask per strike, Black-Scholes Greeks enrichment, PCR (OI & volume), Max Pain, OI-based support/resistance, CE-vs-PE OI bars, ATM highlight (★), expiry selector backed by real instrument expiries when logged in.

### 4.3 📈 Technical
Candlestick chart (LightweightCharts, theme-aware) with interval switching (1m–1D) and BUY/SELL markers from `/api/chart-signals` — multi-indicator confluence (EMA 9/21 cross, price vs EMA50, MACD histogram flip, RSI zones, Supertrend flip; emits when score ≥ 3).

### 4.4 🎯 Signals
Full technical + options analysis per index (`/api/analysis/{index}`): trend, all indicators, options analytics, sentiment, generated trade signal and strategy suggestions.

### 4.5 🗂️ OI History
Daily end-of-day chain snapshots for the past month (synthetic series) — PCR/IV/max-pain drift per day; click a row for the full strike chain that day.

### 4.6 💼 Positions — with brokerage-adjusted Net P&L
- Table columns: Symbol · Product · Qty · Avg · LTP · **P&L (gross)** · **Charges (est)** · **Net P&L** · Day P&L, plus a **TOTAL row** and headline: *"Total Profit after charges: +₹718.95 (gross +₹835.75 − charges ₹116.80)"*.
- **Charge model** (`_estimate_fo_charges`, full round trip; open positions assume exit at LTP):
  - Brokerage ₹20 × 2 executed orders
  - STT 0.1% of sell-side premium
  - Exchange transaction charge 0.03503% of premium turnover
  - GST 18% on (brokerage + txn + SEBI)
  - SEBI ₹10/crore · Stamp duty 0.003% of buy value
- **Position Analysis cards** — per option position: IV, Greeks, scenario P&L table, sell recommendations.

### 4.7 🤖 Algo — the main signal engine (`/api/algo-signals`)
Recomputed from **live market data** on every call: spot from the live quote (matches the navbar), indicators from 15-minute candles, premiums from the live ATM chain.

**Scoring rules (11):**

| # | Rule | Score |
|---|---|---|
| 1 | EMA 9 > 21 > 50 alignment (or inverse) | ±2 |
| 1b | **EMA 20/50 crossover** (within last 3 candles) | ±2 |
| 1c | **Close crosses Supertrend AND EMA20 on the same candle** | ±3 |
| 2 | Price vs EMA200 | ±1 |
| 3 | MACD above/below signal with histogram | ±2 |
| 4 | MACD histogram flip | ±2 |
| 5 | RSI zones (50–70 bullish / 30–50 bearish; extremes reverse) | ±2 / ±1 |
| 6 | Supertrend direction | ±2 |
| 7 | Bollinger position | ±1 |
| 8 | **Opening Range Breakout** (break of 09:15–09:45 high/low) | ±2 |
| 9 | **CPR position** (+ narrow-CPR trending-day flag) | ±1 |
| 10 | **PDH/PDL** (trading above prev-day high / below low) | ±1 |
| 11 | **Gap open** ≥ ±0.3% | ±1 |
| 12 | **Intraday VWAP** (price above/below day VWAP) | ±1 |

**Decision:** buy score ≥ 10 (and > sell+2) → STRONG BUY; sell ≥ 10 → STRONG SELL; otherwise MILD by majority; tie → NEUTRAL.

**Theta-decay clock:** a buy-timing filter shown as an OK / CAUTION / AVOID / CLOSED banner based on IST time and expiry proximity — OK before 14:30, CAUTION after 14:30 or on expiry day, AVOID in the last hour or late on expiry day, CLOSED on weekends/after hours. Tells option *buyers* when theta makes fresh entries unwise.

**Trailing stop-loss ladder:** each leg carries a 3-step trail — at +20% move SL to cost (risk-free), at +40% trail to +20%, at +60% trail to +35% and ride the rest.

**Position-sizing calculator:** a panel at the top of the Algo tab — enter capital, risk % per trade, entry premium, stop-loss, lot size → outputs lots to buy, capital needed, actual ₹ risk (and % of capital), and risk per lot. Formula: `lots = floor((capital × risk%) ÷ ((entry − SL) × lot_size))`. Each trade card has a **🧮 Size it** button that pre-fills the calculator from that trade.

**Recommendation — option BUYING only (no short/futures-style legs):**
- STRONG BUY → single **BUY ATM CE** card · target +60% / SL −30% (R:R exactly 1:2)
- STRONG SELL → single **BUY ATM PE** card · same R:R
- MILD → target +40% / SL −20% (also 1:2)
- NEUTRAL → no trade card, "wait" notice

**Locked trade plan:** when a signal fires, the server records the time and premium (`_algo_signal_state`). The card shows: *⏱ Signal active since HH:MM:SS · Buy Price (at signal) · Premium Now (green = cheaper than plan) · Sell Target · Stop Loss*, and a plan sentence ending with the **sell time rule: exit at target/SL or square off by 15:15 IST**. The plan re-locks only when the signal/strike/expiry changes.

**Display:** indicator chips (RSI, ATR, EMA 20/50 state, ST+EMA20 cross, scores, confidence), **Key Levels strip** (ORB range with ▲/▼, CPR ± NARROW, VWAP ▲/▼, Pivot, R1/S1, PDH/PDL, Gap %), **theta-clock banner**, bull/bear score bar, signal factors list. Each trade card adds the **trailing stop-loss ladder**. **Auto-refreshes every 30 s** (pausable), shows "Updated HH:MM:SS", fires a toast + banner when a market move flips the signal, and preserves card collapse state across refreshes.

### 4.8 🔬 Advanced
GEX (gamma exposure by strike), OI heatmap, IV surface, Multi-Timeframe alignment (5m/15m/1h/D), Strategy payoff builder, 30-day algo backtest, Economic calendar.

### 4.9 🧠 Smart OI
- **Left panel:** price **line chart** (area, upper ~70%) + **volume bars** (bottom ~25%) in one chart.
- **Right panels:** CE/PE/Net OI time-series + PCR time-series (Chart.js).
- **Crosshair sync:** hovering the price chart highlights the same time point on the OI and PCR charts with tooltips.
- Index/expiry/interval/live-historical controls; strike-wise CE-PE ratio interpretation (RESISTANCE / SUPPORT / CALL WRITING / PUT WRITING / BOTH ADDING / UNWINDING) via `/api/smart-oi`.
- **ATM Straddle Tracker** (`/api/straddle/{index}`) — the "is today a buying day?" filter. Charts the ATM CE+PE combined premium and ATM IV through the session, with a verdict (THETA CRUSH → avoid buying · EXPANSION → buy the direction · RANGEBOUND → weak day) and an **IV percentile + IV-crush warning** (high percentile → "premiums inflated, prefer spreads"). Backed by `snapshot_store.py` (SQLite) which records straddle/IV/OI/PCR snapshots (throttled to ~150 s); uses real stored snapshots once ≥3 exist for the day, otherwise a synthesized decay series. ATM IV is backed out from the straddle: `IV ≈ straddle ÷ (spot · √(DTE/365) · 0.8)`.

### 4.10 🏆 Stock F&O Picks (`/api/stock-fo-picks`)
**10 stocks:** RELIANCE, HDFCBANK, ICICIBANK, INFY, TCS, SBIN, BHARTIARTL, AXISBANK, KOTAKBANK, LT. Live mode pulls real futures contracts (lot size, OI), the near-expiry option chain around ATM, daily futures candles for technicals, and the day VWAP; deterministic per-day mock otherwise.

**Scored market rules:** EMA trend ±2 · EMA 20/50 crossover ±2 · Supertrend+EMA20 same-candle cross ±2 · RSI ≥60/≤40 ±1 · futures OI buildup (Long/Short Buildup ±2, Covering/Unwinding ±1) · PCR >1.2/<0.8 ±1 · futures basis ±1 (>0.2%) · day momentum ±1 (>±0.75%) · S&R breakout on ≥1.2× volume ±2 · option delta ≥0.60/≤−0.60 ±1 · VWAP quick formula ±2.

**7 strategy methods displayed per card:**
1. **Moving Average** — price vs EMA20 & EMA50
2. **EMA 20/50 Crossover** — cross above → Buy CE / below → Buy PE
3. **Supertrend + EMA20 Cross** — close crossing both on one candle
4. **S&R Breakout + Volume** — OI-derived S/R broken on ≥1.2× avg volume
5. **Risk-Reward ≥ 1:2** — Risk = Entry−SL, Reward = Target−Entry; trade only if Reward÷Risk ≥ 2 (target +60% / SL −30% ⇒ exactly 1:2; failing trades downgraded to WAIT)
6. **Option Delta** — Black-Scholes Δ (≈0.50 = ATM; ≥0.60 stronger CE; ≤−0.60 stronger PE)
7. **VWAP Quick Formula** — price > VWAP + RSI > 60 + volume rising → Buy CE (inverse → Buy PE)

**Decision:** score ≥ +3 BUY CE (≥ +6 HIGH) · ≤ −3 BUY PE (≤ −6 HIGH) · else WAIT.
**Trade block:** contract, entry/target/SL, risk/reward ₹, R:R badge, spot target (OI resistance for CE / support for PE), lot size, 1-lot cost, **⏱ buy-signal-since time with the locked premium and "exit by 15:15 IST"**, and the fired reasons.
**UI:** cards sorted by conviction, futures/RSI/PCR/S-R stats, per-card collapse (▾/▸) + Collapse-all/Expand-all, summary strip (e.g. "6 BUY CE · 3 BUY PE · 1 WAIT"), MOCK DATA badge when not authenticated, rules legend.

### 4.11 ⏰ Time Slots (`/api/time-span-picks`)
CE/PE recommendation for NIFTY & SENSEX per intraday session:

| Session | Window | Sell by |
|---|---|---|
| Morning | 09:15 – 11:45 | 11:45 |
| Midday | 11:45 – 14:00 | 14:00 |
| Afternoon | 14:00 – 15:30 | 15:30 |

Per session score = **today's move inside that window** (±2 if > ±0.05%) + **the same window's direction over the last ~10 trading days** (±1 if ≥60% one-sided). Score ≥ +1 → BUY CE · ≤ −1 → BUY PE · else WAIT. Rows show status (● LIVE NOW highlighted / UPCOMING / DONE), ATM option, buy premium, target +40% / SL −20% (1:2), the sell-by time, and the reasoning (e.g. "fell 7/10 recent days in this window").

---

## 5. Formula Reference

### 5.1 Indicators
- **EMA(n):** standard exponential, `span=n`. **RSI(14):** Wilder. **MACD:** 12/26/9. **Bollinger:** 20-period, ±2σ. **ATR(14)**. **Supertrend:** period 10, multiplier 3 (band + direction). **Stochastic, VWAP, classic Pivots** also implemented in `technical.py`.

### 5.2 CPR / Pivots (from previous day H/L/C)
`Pivot=(H+L+C)/3 · BC=(H+L)/2 · TC=2·Pivot−BC · R1=2P−L · S1=2P−H · R2=P+(H−L) · S2=P−(H−L)`
Narrow CPR = width < 0.25% of prev close → trending-day flag.

### 5.3 Options
- **Greeks:** Black-Scholes (Δ Γ Θ V ρ), risk-free 6.5%.
- **PCR:** ΣPE OI ÷ ΣCE OI (and volume variant).
- **Max Pain:** strike minimizing total writer payout.
- **Expected move:** `spot × IV × √(DTE/365)`.
- **IV:** Newton-Raphson implied vol from premium.

### 5.4 Futures buildup matrix
Price ≥ +0.1% & OI ≥ +1% → LONG BUILDUP · price ≤ −0.1% & OI up → SHORT BUILDUP · price up & OI ≤ −1% → SHORT COVERING · both down → LONG UNWINDING.

### 5.5 Charges (per round trip)
`₹40 brokerage + 0.1%·sell_value (STT) + 0.03503%·turnover (exchange) + 18% GST on (brokerage+txn+SEBI) + ₹10/cr SEBI + 0.003%·buy_value (stamp)`

### 5.6 Composite sentiment
PCR (±2 at >1.3/<0.7) + VIX (+1 <13 … −2 >22) + A/D ratio (±2) + FII net (±2 at ±2000 Cr) + trend ±0.5 (accepts BULLISH/UPTREND vocabularies). Gauge needle = `50 + normalized×0.45` (continuous, clamped 5–95). Labels symmetric: ≥+4 STRONGLY_BULLISH · ≥+1.5 BULLISH · ±1.5 NEUTRAL · ≤−1.5 BEARISH · ≤−4 STRONGLY_BEARISH. **PCR comes from the actual option chain** (not random).

---

## 6. API Endpoint Reference

| Endpoint | Purpose |
|---|---|
| `GET /health`, `GET /api/config` | Liveness, runtime config |
| `GET /api/auth/login` / `callback` / `status`, `POST /api/auth/logout` | Zerodha session |
| `GET /api/market/quote/{index}` | Live/mock quote |
| `GET /api/historical/{index}` | OHLCV candles |
| `GET /api/chart-signals/{index}` | Chart BUY/SELL markers |
| `GET /api/analysis/{index}` | Full technical + options analysis |
| `GET /api/expiry-list/{index}` | Real expiries from instruments |
| `GET /api/option-chain/{index}` (+history) | Chain with Greeks/analytics |
| `GET /api/positions` | Positions + **charges & net P&L summary** |
| `GET /api/position-analysis` | Greeks/scenarios per position |
| `POST /api/order` | Place order (confirm modal in UI) |
| `GET /api/algo-signals` | 11-rule engine, locked plans, key levels |
| `GET /api/stock-fo-picks` | 10 stocks, 7 methods, locked buy times |
| `GET /api/time-span-picks` | Session CE/PE picks |
| `GET /api/smart-oi/{index}` (+`-timeseries`) | Strike-wise OI + intraday series |
| `GET /api/gex` / `oi-heatmap` / `iv-surface` / `mtf-signals` / `strategy-payoff` / `backtest` / `economic-calendar` | Advanced tab |
| `GET /api/dashboard` | Combined dashboard payload |
| `WS /ws/live` | 2-second quote stream |

---

## 7. UI / UX Features

- **Dark/Light theme** — navbar ☀️/🌙 toggle, saved in `localStorage`, applied pre-paint (no flash). All colors flow through CSS variables; charts read the palette via `chartTheme()` and rebuild on toggle. Light palette tuned for contrast (amber replaces gold, deeper green/red/blue).
- **Collapsible cards** — Algo blocks and all Stock Picks cards (▾/▸ + Collapse all/Expand all); state survives auto-refresh.
- **Live behavior** — navbar prices flash on tick; Algo auto-refresh 30 s with change toasts; "Updated" clocks on Algo/Positions/Stock Picks/Time Slots.
- **Order flow** — algo card button → confirmation modal → `POST /api/order` (NRML/MIS, market/limit).

---

## 8. Known Limitations / Roadmap

- FII/DII and advance/decline are **simulated** even when logged in.
- OI History and the Smart OI time series are synthetic (no chain snapshot persistence yet).
- IV rank uses a synthetic IV history; no IV-crush warning.
- Locked trade plans live in server memory — a restart re-locks at current prices.
- Mock mode: daily and intraday simulations are independent, so key levels can look offset from spot (live data is always consistent).
- Full prioritized backlog: **[PENDING_ANALYSIS_REPORT.md](PENDING_ANALYSIS_REPORT.md)** (next up: chain snapshotting → straddle tracker & IV skew; theta clock; trailing SL; position sizing).

---

*Educational tooling — not investment advice. All targets/stop-losses follow the 1:2 risk-reward rule throughout the application.*
