# Pending Analysis Report — F&O Analyzer
**Gap analysis vs. what expert option traders use for CE/PE buying**
*Generated: 12 Jun 2026*

---

## 1. What the application already covers

| Area | Implemented |
|---|---|
| **Trend / Momentum** | EMA 9/21/50/200, EMA 20/50 crossover, Supertrend(10,3), Supertrend+EMA20 same-candle cross, MACD, RSI, Bollinger Bands, ATR, Stochastic* |
| **Options data** | Option chain + Greeks (Δ Γ Θ V ρ), PCR (OI & volume), Max Pain, OI buildup S/R, IV rank, expected move, Smart OI (strike-wise CE/PE ratio + interpretation), OI time series, GEX, OI heatmap, IV surface, strategy payoff |
| **Futures** | Basis (premium/discount), OI change, Long/Short Buildup / Covering / Unwinding |
| **Sentiment** | Composite gauge (PCR + VIX + A/D + FII + trend), India VIX level |
| **Signal engines** | Algo (NIFTY/SENSEX, locked trade plans, 1:2 RR), Stock F&O Picks (10 stocks, 7 methods), Time Slots (session CE/PE), Chart signals, MTF alignment (5m/15m/1h/D), 30-day algo backtest |
| **Risk / execution** | 1:2 risk-reward gate, target/SL %, lot cost, brokerage-adjusted net P&L, position Greeks & scenarios, order placement |

\* *Computed in `technical.py` but **not used in any CE/PE decision rule** — see item 2 below.*

---

## 2. PENDING — high priority (expert CE/PE buyers use these daily)

### 2.1 Opening Range Breakout (ORB) ⭐ — ✅ ADDED 12 Jun 2026 (Algo rule ±2 + Key Levels chip)
- **What:** High/low of the first 15/30 minutes (09:15–09:30/09:45). Break above range → Buy CE; break below → Buy PE; volume confirmation.
- **Why experts use it:** The single most popular intraday entry for index options in India. Defines a clear buy *time* and *level*.
- **Where it fits:** New rule in Algo (±2) + a level line on charts + Time Slots morning session.
- **Effort:** Low (candles already available).

### 2.2 CPR + Pivot Points (daily) — ✅ ADDED 12 Jun 2026 (Algo rule ±1, narrow-CPR flag, Key Levels chips)
- **What:** Central Pivot Range (Pivot/BC/TC) + R1-R3/S1-S3. Narrow CPR → trending day (buy options); wide CPR → sideways (avoid buying). Price above CPR = CE bias, below = PE bias.
- **Why:** The standard level framework for Indian index traders. **Already coded in `technical.py` (`calculate_pivot_points`) but never surfaced** in any signal or UI.
- **Where:** Algo rule (±1), levels on Technical chart, CPR-width "trend day?" flag on dashboard.
- **Effort:** Very low — function exists, just wire it up.

### 2.3 Previous Day High/Low (PDH/PDL) + Gap analysis — ✅ ADDED 12 Jun 2026 (Algo rules ±1, Key Levels chips)
- **What:** PDH/PDL/PDC levels; open gap-up/gap-down %, gap-fill tendency. Open above PDH = strong CE bias; failure at PDH = PE reversal.
- **Why:** Core price-action reference for every professional; gap stats decide first-hour strategy.
- **Where:** Algo rule (±1), dashboard "Today's key levels" card, chart lines.
- **Effort:** Low.

### 2.4 ATM Straddle price tracker
- **What:** ATM CE + PE combined premium through the day. Straddle falling = theta crush / rangebound (don't buy options); straddle rising with price move = real breakout (buy the direction).
- **Why:** Experts use straddle charts to decide *whether to buy options at all today* — the missing "is this a buying day?" filter.
- **Where:** New panel in Smart OI tab + a "premium environment" flag in Algo.
- **Effort:** Medium.

### 2.5 Candlestick patterns at key levels
- **What:** Engulfing, hammer/shooting star, doji, marubozu detected on 15-min/daily candles **at S/R, pivots, or PDH/PDL**.
- **Why:** Entry-trigger confirmation — experts don't buy CE on a level touch, they wait for the reversal candle.
- **Where:** Chart signal markers + Algo confirmation bonus (±1).
- **Effort:** Medium.

### 2.6 IV skew + IV crush warning
- **What:** ATM/OTM CE IV vs PE IV skew (put skew steepening = fear); IV percentile from real history; **pre-event/expiry IV crush warning** ("IV 78th percentile — premiums inflated, prefer spreads over naked buys").
- **Why:** Buying CE/PE at high IV is the #1 retail mistake; experts check IV before every option purchase. Current IV rank uses a synthetic history.
- **Where:** Option chain header warning + Algo/Stock Picks penalty when IV extreme.
- **Effort:** Medium (needs IV history persistence — start storing daily ATM IV now).

---

## 3. PENDING — medium priority

| # | Analysis | How experts use it for CE/PE | Effort |
|---|---|---|---|
| 3.1 | **Index intraday VWAP** ✅ ADDED 12 Jun 2026 | Algo rule ±1 + Key Levels chip (price vs VWAP → CE/PE bias). | Low |
| 3.2 | **Theta-decay clock** ✅ ADDED 12 Jun 2026 | Algo banner OK/CAUTION/AVOID by IST time + expiry proximity (when not to buy). | Low |
| 3.3 | **Trailing stop-loss plan** ✅ ADDED 12 Jun 2026 | Algo leg shows +20%→cost, +40%→+20%, +60%→+35% trail ladder. | Low |
| 3.4 | **Position sizing calculator** | Capital → max 2-3% risk per trade → lots to buy. Experts size first, pick strike second. | Low |
| 3.5 | **VIX trend (not just level)** | Rising VIX + falling market → PE premiums expand (buy PE early); falling VIX kills CE buys even in uptrend. | Low |
| 3.6 | **Max-pain drift / expiry pinning** | Track max pain daily; price pinning toward max pain on expiry week → avoid directional buys near expiry. | Low |
| 3.7 | **Intraday OI shift alerts** | Writers migrating strikes (e.g. 22500 CE OI unwinding + 22600 CE building = upside breakout). Smart OI shows snapshots; needs change-rate + alert. | Medium |
| 3.8 | **Volume spike confirmation** | Breakouts on >2× average volume are real; used in S&R method for stocks but not for index Algo. | Low |
| 3.9 | **Stochastic / RSI divergence** | Price higher-high + RSI lower-high at resistance → PE entry. Stochastic computed but unused. | Medium |
| 3.10 | **Sector strength (for Stock Picks)** | Bank Nifty vs IT vs Auto relative strength — buy CE only in the leading sector's stocks. | Medium |

---

## 4. PENDING — data/infrastructure gaps

| # | Item | Issue |
|---|---|---|
| 4.1 | **Real FII/DII feed** | Currently simulated even when logged in — sentiment gauge partially mock. NSE publishes daily; scrape or manual entry. |
| 4.2 | **Real advance/decline** | Simulated. Compute from NSE 500 quotes when authenticated. |
| 4.3 | **Option chain history persistence** | OI History tab is synthetic. Start snapshotting the live chain (e.g. every 15 min to SQLite) — enables real OI-change analytics, IV history (2.6), straddle history (2.4). |
| 4.4 | **Strategy-level backtesting** | Only the Algo direction is backtested. No win-rate per rule (EMA cross vs Supertrend vs ORB) — needed to know which analyses actually earn. |
| 4.5 | **Alerts** | No push/Telegram/sound when a signal fires — experts don't watch screens; they get alerted. |

---

## 5. Recommended build order

1. **CPR + Pivots** (2.2) — already coded, just surface it. *(quick win)*
2. **ORB** (2.1) — biggest expert-workflow gap for index CE/PE.
3. **PDH/PDL + Gap** (2.3) — completes the key-levels picture.
4. **Chain snapshot persistence** (4.3) — unlocks 2.4, 2.6, 3.7.
5. **Theta clock + trailing SL + position sizing** (3.2–3.4) — risk side, all low effort.
6. **ATM straddle tracker** (2.4) and **IV skew/crush** (2.6).
7. **Candlestick patterns** (2.5), **divergence** (3.9), **sector strength** (3.10).
8. **Real FII/DII + A/D** (4.1–4.2), **alerts** (4.5), **rule-level backtests** (4.4).

---

*Sections 2–3 are ranked by how frequently professional Indian index/stock option buyers reference each tool for CE/PE entries (levels & timing first, confirmation second, environment filters third).*
