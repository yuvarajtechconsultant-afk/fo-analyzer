# F&O Analyzer — Analysis Strategy Documentation

**Application:** NIFTY & SENSEX Futures & Options Analysis Dashboard  
**Backend:** FastAPI + Python | **Data Source:** Zerodha KiteConnect (live) / Mock Engine (offline)  
**Indices Supported:** NIFTY (lot size: 65) | SENSEX (lot size: 20)

---

## Table of Contents

1. [Option Chain Analysis](#1-option-chain-analysis)
2. [Black-Scholes Greeks Engine](#2-black-scholes-greeks-engine)
3. [Implied Volatility (IV) Calculation](#3-implied-volatility-iv-calculation)
4. [Put-Call Ratio (PCR)](#4-put-call-ratio-pcr)
5. [Max Pain Theory](#5-max-pain-theory)
6. [OI Buildup & Support/Resistance](#6-oi-buildup--supportresistance)
7. [Technical Indicators](#7-technical-indicators)
8. [Master Signal Generator](#8-master-signal-generator)
9. [Algo Signals Engine](#9-algo-signals-engine)
10. [Gamma Exposure (GEX)](#10-gamma-exposure-gex)
11. [IV Surface & Skew Analysis](#11-iv-surface--skew-analysis)
12. [OI Heatmap](#12-oi-heatmap)
13. [Multi-Timeframe Signal Alignment (MTF)](#13-multi-timeframe-signal-alignment-mtf)
14. [Strategy Payoff Analysis](#14-strategy-payoff-analysis)
15. [Signal Backtesting Engine](#15-signal-backtesting-engine)
16. [Economic Calendar](#16-economic-calendar)
17. [Market Breadth & Sentiment](#17-market-breadth--sentiment)
18. [Expected Move Calculator](#18-expected-move-calculator)
19. [Strategy Suggester](#19-strategy-suggester)

---

## 1. Option Chain Analysis

**File:** `main.py` → endpoint `/api/option-chain/{index}`  
**Tab:** Option Chain

### What It Does
Fetches the full option chain for the selected index and expiry date, enriches each strike with live greeks and IV, and computes chain-level analytics including PCR, Max Pain, OI analysis, and ATM strike.

### Data Shown Per Strike
| Column | Description |
|--------|-------------|
| CE LTP | Last traded price of Call option |
| CE OI | Open Interest in Call option (in contracts) |
| CE OI Change | OI change vs previous day (+buildup / -unwinding) |
| CE IV | Implied Volatility of CE (%) |
| Strike | Strike price |
| PE LTP | Last traded price of Put option |
| PE OI | Open Interest in Put option (in contracts) |
| PE OI Change | OI change vs previous day |
| PE IV | Implied Volatility of PE (%) |

### OI Color Coding
- **Red bars (CE side):** High CE OI = resistance zone; larger bars = stronger resistance
- **Green bars (PE side):** High PE OI = support zone; larger bars = stronger support
- **Gold highlight (★):** ATM strike — the most actively traded strike

### Top-Level Metrics
| Metric | Formula | Interpretation |
|--------|---------|---------------|
| PCR (OI) | Total PE OI ÷ Total CE OI | >1.3 = Bullish; <0.7 = Bearish |
| Max Pain | Strike with minimum writer loss | Price tends to gravitate here near expiry |
| Call Resistance | Strike with highest CE OI addition | Market ceiling |
| Put Support | Strike with highest PE OI addition | Market floor |
| DTE | Calendar days to selected expiry | Used in all Greek calculations |

### API Parameters
- `index`: NIFTY or SENSEX
- `expiry`: Date string (YYYY-MM-DD); defaults to nearest expiry

---

## 2. Black-Scholes Greeks Engine

**File:** `analysis/options.py` → `calculate_greeks()`

### Overview
The application implements the full **Black-Scholes-Merton** pricing model to compute option greeks for every strike in the option chain. Inputs use calendar days (not trading days) with risk-free rate set to RBI repo rate (6.5%).

### Greeks Computed

#### Delta (δ)
- **Definition:** Rate of change of option price per ₹1 move in spot
- **CE formula:** `N(d1)` — ranges 0 to 1
- **PE formula:** `N(d1) - 1` — ranges -1 to 0
- **Interpretation:**
  - CE Delta 0.5 = ATM; near 1.0 = deep ITM; near 0 = deep OTM
  - PE Delta -0.5 = ATM; near -1.0 = deep ITM; near 0 = deep OTM
  - A CE with delta 0.5 moves ~₹50 when Nifty moves ₹100

#### Gamma (γ)
- **Definition:** Rate of change of delta per ₹1 move in spot
- **Formula:** `PDF(d1) / (S × σ × √T)` — same for CE and PE
- **Interpretation:**
  - Highest at ATM; drops for deep ITM/OTM options
  - High gamma = option delta changes rapidly with spot moves
  - Used in GEX calculation to find dealer hedging walls

#### Theta (θ)
- **Definition:** Time decay — option premium lost per calendar day
- **Unit:** ₹ per day (negative for option buyers)
- **CE formula:** `[-(S × PDF(d1) × σ) / (2√T) - r × K × e^(-rT) × N(d2)] / 365`
- **PE formula:** `[-(S × PDF(d1) × σ) / (2√T) + r × K × e^(-rT) × N(-d2)] / 365`
- **Interpretation:** A theta of -5 means you lose ₹5/day per lot (time working against buyers)

#### Vega (ν)
- **Definition:** Change in option price per 1% change in IV
- **Formula:** `S × √T × PDF(d1) / 100`
- **Interpretation:** High vega options are very sensitive to IV crush/expansion

#### Rho (ρ)
- **Definition:** Change in option price per 1% change in risk-free interest rate
- **CE formula:** `K × T × e^(-rT) × N(d2) / 100`
- **PE formula:** `-K × T × e^(-rT) × N(-d2) / 100`
- **Interpretation:** Minor impact for short-dated options; more relevant for LEAPS

### Supporting Values
- **Intrinsic Value:** `max(S-K, 0)` for CE; `max(K-S, 0)` for PE
- **Time Value:** `BS Price - Intrinsic Value` (always ≥ 0)

### d1 / d2 Parameters
```
d1 = [ln(S/K) + (r + σ²/2) × T] / (σ × √T)
d2 = d1 - σ × √T

Where:
  S = Spot price
  K = Strike price
  T = Time to expiry in years
  r = Risk-free rate (0.065)
  σ = Implied Volatility (as fraction)
```

---

## 3. Implied Volatility (IV) Calculation

**File:** `analysis/options.py` → `calculate_iv()`

### Method: Brent's Numerical Method
IV is calculated by inverting the Black-Scholes pricing formula. Since there's no closed-form solution for IV, the app uses **Brent's method** (from `scipy.optimize.brentq`) — a robust root-finding algorithm.

### Algorithm
1. Take the market LTP of the option
2. Search for the σ (volatility) that makes `BS_Price(σ) = LTP`
3. Search range: 0.0001 to 5.0 (0.01% to 500% IV)
4. Convergence tolerance: 1e-6 (extremely precise)
5. Maximum iterations: 200

### Edge Cases Handled
| Condition | Behavior |
|-----------|----------|
| LTP ≤ 0 | Returns IV = 0.0 |
| LTP < intrinsic value | Returns IV = 0.0 (arbitrage violation) |
| No convergence | Returns IV = 0.0 (solver failure) |
| DTE = 0 | Uses epsilon (1e-6 years) to avoid division by zero |

### IV Display
- Shown as percentage: IV of 0.18 → displayed as **18.0%**
- ATM IV used as the benchmark for IV surface and expected move calculations

---

## 4. Put-Call Ratio (PCR)

**File:** `analysis/options.py` → `calculate_pcr()`  
**API:** `/api/option-chain/{index}` (embedded in chain response)

### Two PCR Variants

#### PCR by Open Interest (PCR-OI)
```
PCR-OI = Total PE OI across all strikes / Total CE OI across all strikes
```
**Primary indicator** — reflects positioning of option writers (institutional flow)

#### PCR by Volume (PCR-Volume)
```
PCR-Volume = Total PE Volume / Total CE Volume
```
**Secondary indicator** — reflects intraday buying pressure

### Sentiment Interpretation
| PCR-OI Value | Sentiment | Meaning |
|-------------|-----------|---------|
| > 1.3 | EXTREMELY_BULLISH | Heavy put writing = strong support below |
| 1.1 – 1.3 | BULLISH | Moderate put writing |
| 0.9 – 1.1 | NEUTRAL | Balanced call and put writing |
| 0.7 – 0.9 | BEARISH | Moderate call writing = overhead resistance |
| < 0.7 | EXTREMELY_BEARISH | Heavy call writing = market under pressure |

### Key Insight
PCR > 1 means more puts are open than calls. This sounds bearish but is actually interpreted as **bullish** because high put OI means put *sellers* (who profit if market stays up) are dominant. This is a contrarian indicator from an options writer perspective.

---

## 5. Max Pain Theory

**File:** `analysis/options.py` → `calculate_max_pain()`  
**API:** embedded in `/api/option-chain/{index}` response

### Theory
Max Pain is the strike price at which **option writers (sellers) lose the minimum amount** on expiry. On expiry day, the spot price tends to gravitate toward this level because:
- Market makers and institutions hold large short option positions
- They naturally hedge/push the market toward the strike that minimizes their payout

### Formula
For each candidate strike S*:
```
Pain(S*) = Σ [max(S* - K, 0) × CE_OI[K]] + Σ [max(K - S*, 0) × PE_OI[K]]
           (CE writers loss)                  (PE writers loss)
```
Max Pain Strike = S* that **minimizes** `Pain(S*)`

### Algorithm
1. For every strike in the chain, compute total pain if spot expires there
2. The strike with lowest total pain = Max Pain Strike
3. Computed across all 31 strikes simultaneously

### Trading Use
- Near expiry (DTE ≤ 5): Max Pain is a strong magnetic level
- Options far from Max Pain tend to expire worthless
- Useful for selecting credit spread wings

---

## 6. OI Buildup & Support/Resistance

**File:** `analysis/options.py` → `analyze_oi_buildup()`

### OI Change Interpretation
| CE OI Change | Price | Signal |
|-------------|-------|--------|
| +Rising | +Rising | Long Buildup (Bullish) |
| +Rising | -Falling | Short Buildup (Bearish) |
| -Falling | +Rising | Short Covering (Bullish) |
| -Falling | -Falling | Long Unwinding (Bearish) |

### Support and Resistance Detection
The heatmap endpoint (`/api/oi-heatmap/{index}`) tags each strike:

| Tag | Condition |
|-----|-----------|
| STRONG SUPPORT | PE OI change in top 30% AND strike below spot |
| STRONG RESISTANCE | CE OI change in top 30% AND strike above spot |
| SUPPORT | Below spot with PE OI addition |
| RESISTANCE | Above spot with CE OI addition |

### Output
- Top 5 CE strikes by OI change (call resistance zones)
- Top 5 PE strikes by OI change (put support zones)
- **Call Resistance:** Strike with highest CE OI addition
- **Put Support:** Strike with highest PE OI addition

---

## 7. Technical Indicators

**File:** `analysis/technical.py`  
**API:** `/api/technicals/{index}`  
**Tab:** Technical

All indicators are computed from OHLCV historical candle data fetched from Zerodha (or simulated GBM data in offline mode).

---

### 7.1 RSI — Relative Strength Index

**Formula (Wilder's Smoothing):**
```
RSI = 100 - [100 / (1 + RS)]
RS = Avg Gain (EWM α=1/14) / Avg Loss (EWM α=1/14)
```
**Period:** 14 candles  
**Signals:**
| RSI | Signal |
|-----|--------|
| > 70 | OVERBOUGHT (bearish caution) |
| < 30 | OVERSOLD (bullish opportunity) |
| 30–70 | NEUTRAL |

**Score contribution:** +1 (oversold) or -1 (overbought)

---

### 7.2 MACD — Moving Average Convergence Divergence

**Formula:**
```
MACD Line  = EMA(12) - EMA(26)
Signal Line = EMA(9) of MACD Line
Histogram  = MACD Line - Signal Line
```
**Signal Types:**
| Condition | Type | Score |
|-----------|------|-------|
| MACD > Signal AND Histogram > 0 | BULLISH | +1 |
| MACD < Signal AND Histogram < 0 | BEARISH | -1 |
| Histogram crosses above 0 | BULLISH_CROSSOVER | +1 |
| Histogram crosses below 0 | BEARISH_CROSSOVER | -1 |

---

### 7.3 Bollinger Bands

**Formula:**
```
Middle Band = SMA(20)
Upper Band  = SMA(20) + 2 × σ(20)
Lower Band  = SMA(20) - 2 × σ(20)
%B = (Price - Lower) / (Upper - Lower)
Bandwidth = (Upper - Lower) / Middle × 100
```
**Position Signals:**
| %B | Position |
|----|----------|
| > 1.0 | ABOVE_UPPER (overbought / breakout) |
| > 0.8 | NEAR_UPPER |
| 0.2 – 0.8 | MIDDLE (range-bound) |
| < 0.2 | NEAR_LOWER |
| < 0.0 | BELOW_LOWER (oversold / breakdown) |

---

### 7.4 Supertrend

**Formula:**
```
ATR = EWM of True Range (period=10)
HL2 = (High + Low) / 2
Upper Band = HL2 + 3.0 × ATR
Lower Band = HL2 - 3.0 × ATR
```
Direction rules: If price crosses above Upper → BEARISH; If price crosses below Lower → BULLISH

**Settings:** Period=10, Multiplier=3.0  
**Signals:**
- Direction = -1 → BULLISH (price above supertrend) → Score +1
- Direction = +1 → BEARISH (price below supertrend) → Score -1

---

### 7.5 Exponential Moving Averages (EMA)

**Computed:** EMA 20, EMA 50, EMA 200  
**Formula:** `EMA(t) = Price(t) × α + EMA(t-1) × (1-α)` where `α = 2/(period+1)`

**Trend from EMA alignment:**
| Condition | Trend |
|-----------|-------|
| EMA20 > EMA50 > EMA200, Price > EMA20 | UPTREND |
| EMA20 < EMA50 < EMA200, Price < EMA20 | DOWNTREND |
| Otherwise | SIDEWAYS |

---

### 7.6 VWAP — Volume Weighted Average Price

**Formula:**
```
VWAP = Σ(Typical Price × Volume) / Σ(Volume)
Typical Price = (High + Low + Close) / 3
```
- Resets intraday (cumulative from market open)
- Price above VWAP = institutional buying zone → Score +1
- Price below VWAP = institutional selling zone → Score -1

---

### 7.7 ATR — Average True Range

**Formula:**
```
True Range = max(High-Low, |High-Prev Close|, |Low-Prev Close|)
ATR = EWM(True Range, α=1/14)
```
**Period:** 14 candles  
**Usage:**
- Stop-loss calculation: `SL = Entry ± 1.5 × ATR`
- Target calculation: `Target = Entry ± 2.0 × ATR`
- Position sizing reference

---

### 7.8 Stochastic Oscillator

**Formula:**
```
%K = 100 × (Close - Lowest Low(14)) / (Highest High(14) - Lowest Low(14))
%D = SMA(3) of %K
```
**Signals:**
| %K | Signal |
|----|--------|
| > 80 | OVERBOUGHT |
| < 20 | OVERSOLD |
| %K > %D | BULLISH |
| %K < %D | BEARISH |

---

### 7.9 Pivot Points (Three Methods)

Computed from **previous session's** High, Low, Close.

#### Classic Pivots
```
Pivot (P) = (High + Low + Close) / 3
R1 = 2P - Low        S1 = 2P - High
R2 = P + (H - L)     S2 = P - (H - L)
R3 = H + 2(P - L)    S3 = L - 2(H - P)
```

#### Camarilla Pivots
```
R1 = Close + Range × 1.1/12
R2 = Close + Range × 1.1/6
R3 = Close + Range × 1.1/4
R4 = Close + Range × 1.1/2
(S levels mirror with subtraction)
```
Best used for intraday range identification.

#### Fibonacci Pivots
```
R1 = P + 0.382 × Range
R2 = P + 0.618 × Range
R3 = P + 1.000 × Range
S1 = P - 0.382 × Range
S2 = P - 0.618 × Range
S3 = P - 1.000 × Range
```
Based on Fibonacci ratios (38.2%, 61.8%, 100%) of the previous day's range.

---

### 7.10 Support & Resistance Detection

**Algorithm:** Swing High/Low detection with level merging
```
Swing High: price[i] == max(price[i-10 : i+10])
Swing Low:  price[i] == min(price[i-10 : i+10])
```
- Nearby levels within 0.3% of each other are merged (averaged)
- Returns top 5 resistance levels above current price
- Returns top 5 support levels below current price

---

### 7.11 Overall Signal Score

Each indicator contributes to a composite signal score:

| Indicator | Bullish | Bearish |
|-----------|---------|---------|
| RSI | +1 (oversold) | -1 (overbought) |
| MACD | +1 | -1 |
| Supertrend | +1 | -1 |
| EMA trend | +1 | -1 |
| VWAP | +1 | -1 |
| BB position | +1 (near lower) | -1 (near upper) |
| Stochastic | +1 | -1 |

**Overall Bias:**
- Score ≥ +3 → **BULLISH**
- Score ≤ -3 → **BEARISH**
- Otherwise → **NEUTRAL**

---

## 8. Master Signal Generator

**File:** `analysis/signals.py` → `generate_trade_signals()`  
**API:** `/api/signals/{index}`  
**Tab:** Signals

Combines **technical analysis + options data + market sentiment** into a single actionable trade signal.

### Input Sources
1. Technical bias and score from `generate_technical_signals()`
2. PCR, Max Pain, IV Rank, Expected Move from options chain
3. Market sentiment from `get_market_sentiment()`

### Direction Score Build-up
```
direction_score = technical_signal_score
+ sentiment adjustment (±1 if strong)
+ PCR adjustment (PCR > 1.2 → +1; PCR < 0.8 → -1)
+ OI trend adjustment (BULLISH/BEARISH → ±1)
```

### Signal Decision
| Score | Action | Instrument |
|-------|--------|-----------|
| ≥ +3 | BUY | Index CE |
| ≤ -3 | SELL | Index PE |
| -2 to +2 | WAIT | — |

### High IV Override
If IV Rank > 65% and a directional signal exists:
- BUY signal → switches to **Bull Put Spread** (collect premium instead of buying)
- SELL signal → switches to **Bear Call Spread** (collect premium instead of buying)

### Stop-Loss & Target
```
Target   = Entry Price ± 2.0 × ATR
Stop-Loss = Entry Price ∓ 1.5 × ATR
Risk:Reward = |Target - Entry| / |Entry - SL|
```

### Confidence Levels
| |Direction Score| | Confidence |
|----------------|-----------|
| ≥ 5 | HIGH |
| 3–4 | MEDIUM |
| < 3 | LOW |

---

## 9. Algo Signals Engine

**File:** `main.py` → `/api/algo-signals`  
**Tab:** 🤖 Algo

### Purpose
Generates **STRONG BUY / STRONG SELL** signals specifically for CE and PE options, complete with strike selection, entry price, target, and stop-loss. Designed for direct order placement to Zerodha.

### 7-Indicator Scoring Model
Each indicator votes on direction:

| # | Indicator | BUY condition | SELL condition |
|---|-----------|--------------|----------------|
| 1 | RSI | RSI < 40 (oversold) | RSI > 60 (overbought) |
| 2 | MACD | Histogram > 0 | Histogram < 0 |
| 3 | EMA Alignment | EMA9 > EMA21 > EMA50 | EMA9 < EMA21 < EMA50 |
| 4 | PCR | PCR > 1.1 (put heavy) | PCR < 0.9 (call heavy) |
| 5 | Supertrend | Bullish direction | Bearish direction |
| 6 | Bollinger %B | %B < 0.2 (near lower) | %B > 0.8 (near upper) |
| 7 | OI Buildup | Net PE OI > CE OI | Net CE OI > PE OI |

### Signal Threshold
- Score ≥ 5 out of 7 → **STRONG BUY CE** + **STRONG SELL PE**
- Score ≤ 2 out of 7 → **STRONG SELL CE** + **STRONG BUY PE**
- Otherwise → **NEUTRAL** (no signal)

### CE Signal Prices
```
Entry  = ATM CE last traded price (LTP)
Target = Entry × 1.4  (40% profit target)
SL     = Entry × 0.7  (30% stop-loss)
Strike = ATM strike
```

### PE Signal Prices
```
Entry  = ATM PE last traded price (LTP)
Target = Entry × 1.4  (40% profit target)
SL     = Entry × 0.7  (30% stop-loss)
Strike = ATM strike
```

### Order Placement
Clicking BUY/SELL in the Algo tab opens the Order Modal:
- **Lots:** 1–10 (qty = lots × lot size)
- **Order Type:** MARKET or LIMIT
- **Product:** MIS (intraday) or NRML (positional)
- **Exchange:** NFO (NIFTY) or BFO (SENSEX)
- POSTs to `/api/order` which calls Zerodha `kite.place_order()`

---

## 10. Gamma Exposure (GEX)

**File:** `main.py` → `/api/gex/{index}`  
**Tab:** 🔬 Advanced → GEX Map

### What Is GEX?
Gamma Exposure measures the **total dollar-gamma** that market makers (dealers) hold across all strikes. It shows where dealers must buy/sell the underlying to hedge their delta, creating **price walls**.

### Formula
```
CE GEX per strike = CE_Gamma × CE_OI × Lot_Size × Spot² / 1e9   (in ₹ Crore)
PE GEX per strike = PE_Gamma × PE_OI × Lot_Size × Spot² / 1e9   (in ₹ Crore)
Net GEX           = CE_GEX - PE_GEX
```
Division by 1e9 converts to Crore scale for readability.

### GEX Regimes
| Regime | Condition | Market Behavior |
|--------|-----------|----------------|
| Positive GEX | Net GEX > 0 | Dealers long gamma → buy dips, sell rallies → **Market stabilizes** |
| Negative GEX | Net GEX < 0 | Dealers short gamma → must chase moves → **Market accelerates** |

### GEX Flip Level
The first strike where `net_gex < 0` is the **GEX Flip** — the point where the market can transition from stabilizing to accelerating behavior.

### Chart
Stacked bar chart: CE GEX shown as positive green bars, PE GEX shown as negative red bars. Taller bars = stronger dealer hedging walls = stronger price resistance/support.

### Top GEX Walls
The 5 strikes with the highest absolute net GEX — these are the strongest potential price magnets or barriers.

---

## 11. IV Surface & Skew Analysis

**File:** `main.py` → `/api/iv-surface/{index}`  
**Tab:** 🔬 Advanced → IV Surface

### IV Smile / Skew
When plotted across strikes, the implied volatility curve forms a shape:
- **IV Smile:** Symmetric — OTM options on both sides have higher IV than ATM (common in equity indices after Black Monday 1987)
- **Put Skew:** PE IV > CE IV at same moneyness — market pricing in downside protection

### Metrics Computed

#### IV Percentile (IVP)
```
IVP = (ATM_IV - 52W_Low_IV) / (52W_High_IV - 52W_Low_IV) × 100
```
| IVP | Regime | Strategy |
|-----|--------|---------|
| > 60% | HIGH IV | Sell options (collect premium) |
| 40–60% | NORMAL IV | Mixed |
| < 40% | LOW IV | Buy options (cheap premium) |

#### Skew Direction
```
Skew = PE_IV - CE_IV at ATM strike
PUT SKEW  : PE IV > CE IV (downside fear premium)
CALL SKEW : CE IV > PE IV (upside momentum premium)
```

#### Per-Strike Moneyness
```
Moneyness = (Strike - Spot) / Spot × 100  (as %)
```
Negative % = OTM Put / ITM Call; Positive % = OTM Call / ITM Put

### Chart
Three lines: **CE IV** (green), **PE IV** (red), **IV Skew** (yellow dashed) — plotted against moneyness (%) on the X-axis.

---

## 12. OI Heatmap

**File:** `main.py` → `/api/oi-heatmap/{index}`  
**Tab:** 🔬 Advanced → OI Heatmap

### Purpose
Visualizes the distribution of Open Interest and OI changes across strikes to identify where option writers (institutions) are defending positions — translating to real-world support and resistance.

### Heat Score
```
CE Heat = |CE_OI_Change| / max(|CE_OI_Changes|) × 100
PE Heat = |PE_OI_Change| / max(|PE_OI_Changes|) × 100
```
Higher heat = more aggressive option writing at that strike.

### Strike Tags
| Tag | Logic |
|-----|-------|
| STRONG SUPPORT | PE Heat > 70 AND PE OI increasing AND below spot |
| STRONG RESISTANCE | CE Heat > 70 AND CE OI increasing AND above spot |
| SUPPORT | Below spot AND PE OI increasing |
| RESISTANCE | Above spot AND CE OI increasing |

### Chart
Horizontal bar chart per strike — CE bars on the left (red), PE bars on the right (green). Bars sized by heat intensity. Color intensity indicates strength of the support/resistance zone.

---

## 13. Multi-Timeframe Signal Alignment (MTF)

**File:** `main.py` → `/api/mtf-signals/{index}`  
**Tab:** 🔬 Advanced → MTF Signals

### Timeframes Analyzed
| Timeframe | Lookback | Use Case |
|-----------|----------|---------|
| 5 Min | 3 days | Entry timing, intraday scalping |
| 15 Min | 5 days | Intraday swing trades |
| 1 Hour | 20 days | Intraday–positional trade direction |
| Daily | 60 days | Macro trend confirmation |

### Per-Timeframe Scoring (5 indicators)

| Indicator | Bullish | Score | Bearish | Score |
|-----------|---------|-------|---------|-------|
| EMA Alignment (9/21/50) | EMA9 > EMA21 > EMA50 | +2 | EMA9 < EMA21 < EMA50 | -2 |
| MACD Histogram | Histogram > 0 | +1 | Histogram < 0 | -1 |
| RSI | RSI > 55 | +1 | RSI < 45 | -1 |
| Supertrend | Bullish | +1 | Bearish | -1 |
| Price vs EMA50 | Price > EMA50 | +1 | Price < EMA50 | -1 |

**Max score per timeframe:** +6 (strong bullish) to -6 (strong bearish)  
**Bias:** Score ≥ 3 → BULLISH; ≤ -3 → BEARISH; else NEUTRAL

### Overall Alignment Signal
| Condition | Signal |
|-----------|--------|
| All 4 TFs bullish | **STRONG BUY — All TFs aligned bullish** |
| All 4 TFs bearish | **STRONG SELL — All TFs aligned bearish** |
| Total score > 4 | MILD BUY — Majority bullish |
| Total score < -4 | MILD SELL — Majority bearish |
| Otherwise | MIXED — Wait for alignment |

### Trading Principle
**Timeframe alignment** = highest probability setups. A trade signal aligned on 5min, 15min, 1hr, AND Daily is far stronger than one on a single timeframe.

---

## 14. Strategy Payoff Analysis

**File:** `main.py` → `/api/strategy-payoff/{index}`  
**Tab:** 🔬 Advanced → Strategy P&L

Computes theoretical **profit and loss at expiry** across a range of spot prices (±10% from current) for 6 standard option strategies.

### Strategies Available

#### 1. Iron Condor
**Type:** Credit Spread | **Direction:** Neutral  
**Legs:**
```
SELL CE at ATM + 2×gap (collect premium)
SELL PE at ATM - 2×gap (collect premium)
BUY  CE at ATM + 4×gap (limit upside loss)
BUY  PE at ATM - 4×gap (limit downside loss)
```
**Max Profit:** Net premium collected (all options expire worthless)  
**Max Loss:** Wing spread - net premium (if market breaks far OTM)  
**Best For:** High IV, range-bound market; IV Rank > 70%

#### 2. Short Straddle
**Type:** Credit Spread | **Direction:** Neutral  
**Legs:**
```
SELL ATM CE + SELL ATM PE
```
**Max Profit:** Total premium collected  
**Max Loss:** Unlimited (both sides)  
**Breakeven:** ATM ± total premium  
**Best For:** High IV, low expected volatility; very near expiry

#### 3. Short Strangle
**Type:** Credit Spread | **Direction:** Neutral  
**Legs:**
```
SELL OTM CE (ATM + 2×gap) + SELL OTM PE (ATM - 2×gap)
```
**Max Profit:** Net premium collected  
**Max Loss:** Unlimited (both sides)  
**Best For:** High IV, wider range expected vs straddle

#### 4. Bull Call Spread
**Type:** Debit Spread | **Direction:** Bullish  
**Legs:**
```
BUY  ATM CE
SELL OTM CE (ATM + 2×gap)
```
**Max Profit:** Spread width - net premium  
**Max Loss:** Net premium paid  
**Breakeven:** Buy strike + net premium  
**Best For:** Moderately bullish view, lower cost than naked CE buy

#### 5. Bear Put Spread
**Type:** Debit Spread | **Direction:** Bearish  
**Legs:**
```
BUY  ATM PE
SELL OTM PE (ATM - 2×gap)
```
**Max Profit:** Spread width - net premium  
**Max Loss:** Net premium paid  
**Breakeven:** Buy strike - net premium  
**Best For:** Moderately bearish view, lower cost than naked PE buy

#### 6. Long Butterfly
**Type:** Debit Spread | **Direction:** Neutral (low volatility)  
**Legs:**
```
BUY  1 ITM CE (ATM - 2×gap)
SELL 2 ATM CE
BUY  1 OTM CE (ATM + 2×gap)
```
**Max Profit:** At ATM on expiry (maximum when spot = middle strike)  
**Max Loss:** Net debit paid (very low cost strategy)  
**Best For:** Low IV, expect spot to close near ATM on expiry

### Payoff Calculation
```
For each spot price in range [spot-10%, spot+10%]:
  P&L = Σ [ mult × (intrinsic - premium) × qty × lot_size ]
  Where:
    mult = +1 (BUY legs) or -1 (SELL legs)
    intrinsic = max(spot-K, 0) for CE; max(K-spot, 0) for PE
```

### Key Metrics Computed
- **Max Profit:** Maximum P&L achievable across all scenarios
- **Max Loss:** Worst-case P&L
- **Breakeven Points:** Prices where P&L crosses zero (detected by sign change)
- **Net Premium:** Total cash flow from all legs × lot size

---

## 15. Signal Backtesting Engine

**File:** `main.py` → `/api/backtest/{index}`  
**Tab:** 🔬 Advanced → Backtest

### Purpose
Tests the 7-indicator Algo signal strategy on **historical daily candle data** (last 30–90 days) to evaluate statistical performance before live trading.

### Signal Generation (Per Historical Bar)
Signals are generated using 6 indicators:

| Indicator | BUY score | SELL score |
|-----------|----------|-----------|
| EMA9 > EMA21 > EMA50 | +2 | -2 (reverse) |
| Price > EMA200 | +1 | -1 |
| MACD line > Signal AND Histogram > 0 | +2 | -2 (reverse) |
| RSI 50–70 | +2 | — |
| RSI 30–50 | — | +2 |
| Supertrend Bullish | +2 | -2 |
| Bollinger: Close > Upper | — | +2 |
| Bollinger: Close < Lower | +2 | — |

**Entry threshold:** Buy if score ≥ 5; Sell if score ≤ -5

### Trade Execution Rules
```
Entry: Close price of signal bar
Target: Entry × (1 + 2×ATR/Entry)   [~2 ATR profit target]
Stop-Loss: Entry × (1 - 1.5×ATR/Entry) [~1.5 ATR stop-loss]
Exit: Next day open (simplified; no intrabar exit)
```

### Performance Metrics
| Metric | Formula |
|--------|---------|
| Total Trades | Count of all signals fired |
| Win Rate | Winning trades / Total trades × 100% |
| Avg P&L per Trade | Total P&L / Total trades |
| Max Profit | Best single trade |
| Max Loss | Worst single trade |
| Profit Factor | Total Gains / Total Losses |

### Profit Factor Interpretation
| Profit Factor | Quality |
|--------------|---------|
| > 2.0 | Excellent |
| 1.5 – 2.0 | Good |
| 1.0 – 1.5 | Marginal |
| < 1.0 | System losing money |

### Trade Log
Each backtest trade includes: date, action (BUY/SELL), entry price, target, stop-loss, actual exit, P&L, result (WIN/LOSS), and score.

---

## 16. Economic Calendar

**File:** `main.py` → `/api/economic-calendar`  
**Tab:** 🔬 Advanced → Calendar

Tracks upcoming high-impact events that can cause sudden market moves (impacting both index direction and IV expansion).

### Event Categories
| Type | Color | Examples |
|------|-------|---------|
| expiry | Purple | NIFTY Thursday, SENSEX Friday weekly expiry |
| rbi | Blue | RBI MPC Meeting, Policy Decision |
| macro | Orange | CPI, IIP, GDP data releases |
| global | Red | US FOMC, US CPI |

### Impact Levels
| Impact | Trading Implication |
|--------|---------------------|
| EXTREME | Union Budget — expect 2-3% market move; IV spikes days before |
| HIGH | RBI Policy, US Fed — avoid short-premium strategies day before |
| MEDIUM | Sector-specific; moderate IV impact |

### Auto-Expiry Generation
All NIFTY (Thursday) and SENSEX (Friday) expiry dates within the next 28 days are automatically computed and listed.

### Why This Matters for F&O
- **Before events:** IV rises → option premiums inflate → avoid buying
- **After events:** IV crashes (IV crush) → option sellers profit
- **On expiry day:** OI zeroes out; Max Pain becomes strongest magnet

---

## 17. Market Breadth & Sentiment

**File:** `analysis/market_breadth.py`  
**API:** embedded in `/api/signals/{index}`

### 17.1 FII/DII Activity

Tracks institutional buying/selling pressure in the cash market:
```
FII Net = FII Buy Value - FII Sell Value  (₹ Crore)
DII Net = DII Buy Value - DII Sell Value  (₹ Crore)
```
| FII Net | Sentiment |
|---------|-----------|
| > ₹500 Cr | STRONG_BUYING |
| 0 – 500 Cr | BUYING |
| -500 – 0 Cr | SELLING |
| < -₹500 Cr | STRONG_SELLING |

5-day rolling net shows recent trend vs historical positioning.

### 17.2 Advance-Decline Ratio

```
A/D Ratio = Advancing stocks / Declining stocks
Net Advances = Advancing - Declining
% Advancing = Advancing / Total × 100
```
| A/D Ratio | Breadth |
|-----------|---------|
| > 2.0 | STRONGLY_POSITIVE |
| 1.5 – 2.0 | POSITIVE |
| 1.0 – 1.5 | SLIGHTLY_POSITIVE |
| 0.67 – 1.0 | SLIGHTLY_NEGATIVE |
| < 0.5 | STRONGLY_NEGATIVE |

### 17.3 Composite Market Sentiment Score

Aggregates all sentiment signals:

| Component | Weight | Bullish | Bearish |
|-----------|--------|---------|---------|
| FII Flow | 30% | Net buying | Net selling |
| DII Flow | 20% | Net buying | Net selling |
| Advance/Decline | 25% | A/D > 1.5 | A/D < 0.7 |
| VIX Level | 25% | Low VIX | High VIX |

**Output:** Composite score with label (BULLISH / BEARISH / NEUTRAL)

---

## 18. Expected Move Calculator

**File:** `analysis/options.py` → `calculate_expected_move()`

### Formula (1 Standard Deviation Move)
```
Expected Move = Spot × ATM_IV × √(DTE / 365)
Upper Band = Spot + Expected Move
Lower Band = Spot - Expected Move
```

### Interpretation
There is a **68% statistical probability** that the index will stay within the expected move range on expiry (1 standard deviation = 68% confidence in normal distribution).

**Example:**
- Spot = 23,000; ATM IV = 15%; DTE = 7 days
- Expected Move = 23,000 × 0.15 × √(7/365) = ±342 points
- 68% chance Nifty stays between 22,658 and 23,342

### Trading Use
- **Iron Condor wings:** Place beyond 1 SD move to have 68%+ probability of full profit
- **Long Straddle:** Profitable only if spot moves more than the expected move

---

## 19. Strategy Suggester

**File:** `analysis/options.py` → `suggest_strategies()`  
**API:** embedded in `/api/signals/{index}`

### Market Condition Matrix

| IV Rank | Trend | Strategy Suggested |
|---------|-------|--------------------|
| < 40% (Low) | BULLISH | ATM Call Buy, Bull Call Spread |
| < 40% (Low) | NEUTRAL | Long Straddle |
| > 60% (High) | BULLISH | Bull Put Spread (credit), ATM Call Buy |
| > 60% (High) | BEARISH | Bear Call Spread (credit), ATM Put Buy |
| > 60% (High) | NEUTRAL | Short Straddle, Iron Condor |
| Any | Unclear | Wait & Watch |

### Strategy Leg Details

| Strategy | Legs | Type |
|----------|------|------|
| Bull Call Spread | BUY ATM CE + SELL ATM+100 CE | DEBIT |
| Bear Put Spread | BUY ATM PE + SELL ATM-100 PE | DEBIT |
| ATM Call Buy | BUY ATM CE | NAKED_BUY |
| ATM Put Buy | BUY ATM PE | NAKED_BUY |
| Short Straddle | SELL ATM CE + SELL ATM PE | CREDIT |
| Iron Condor | SELL ATM±100 + BUY ATM±200 | CREDIT |
| Long Straddle | BUY ATM CE + BUY ATM PE | DEBIT |
| Bull Put Spread | SELL ATM-50 PE + BUY ATM-150 PE | CREDIT |
| Bear Call Spread | SELL ATM+50 CE + BUY ATM+150 CE | CREDIT |

### Confidence Assignment
- **HIGH:** When PCR strongly confirms the strategy direction (PCR > 1.2 for bullish; PCR < 0.8 for bearish; IV Rank > 70% for neutral)
- **MEDIUM:** Signal present but PCR not fully confirming

---

## Configuration Reference

**File:** `config.py`

```python
LOT_SIZES = {
    "NIFTY":    65,    # 1 lot = 65 shares
    "SENSEX":   20,    # 1 lot = 20 shares
    "BANKNIFTY": 15,
    "FINNIFTY":  40,
}

STRIKE_INTERVALS = {
    "NIFTY":    50,    # Strikes every 50 points
    "SENSEX":  100,    # Strikes every 100 points
}

RISK_FREE_RATE = 0.065     # RBI repo rate approximation (6.5%)
STRIKES_AROUND_ATM = 15    # 15 strikes on each side of ATM
```

---

## API Endpoints Summary

| Endpoint | Tab | Description |
|----------|-----|-------------|
| `GET /api/option-chain/{index}` | Option Chain | Full chain with greeks, PCR, Max Pain |
| `GET /api/technicals/{index}` | Technical | All technical indicators + signals |
| `GET /api/signals/{index}` | Signals | Master trade signal with entry/target/SL |
| `GET /api/algo-signals` | 🤖 Algo | STRONG BUY/SELL CE+PE with prices |
| `GET /api/gex/{index}` | 🔬 Advanced | Gamma Exposure per strike |
| `GET /api/oi-heatmap/{index}` | 🔬 Advanced | OI change heatmap with SR tags |
| `GET /api/iv-surface/{index}` | 🔬 Advanced | IV smile, skew, IV percentile |
| `GET /api/mtf-signals/{index}` | 🔬 Advanced | 4-timeframe signal alignment |
| `GET /api/strategy-payoff/{index}` | 🔬 Advanced | Strategy P&L payoff chart data |
| `GET /api/backtest/{index}` | 🔬 Advanced | 30-day signal backtest results |
| `GET /api/economic-calendar` | 🔬 Advanced | Upcoming market events |
| `POST /api/order` | 🤖 Algo | Place order directly to Zerodha |

---

## Disclaimer

This application is built for **educational and analytical purposes**. All signals, strategies, and analysis are based on mathematical models and historical data. Options trading involves significant risk. Past performance does not guarantee future results. Always use your own judgment and consult a SEBI-registered advisor before placing trades.

---

*Documentation generated for F&O Analyzer v1.0.0 — NIFTY & SENSEX Dashboard*
