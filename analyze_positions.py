from analysis.options import calculate_iv, calculate_greeks
import datetime

today = datetime.date.today()

# ── NIFTY23200PE ──────────────────────────────────────────────────────────
spot_nifty = 23200.0
strike_n   = 23200
expiry_n   = datetime.date(2026, 6, 9)
dte_n      = max((expiry_n - today).days, 0)
ltp_pe     = 106.45
avg_pe     = 108.5
qty_pe     = 130

iv_pe = calculate_iv(ltp_pe, spot_nifty, strike_n, dte_n, 0.065, "PE")
g_pe  = calculate_greeks(spot_nifty, strike_n, dte_n, iv_pe, 0.065, "PE")

print("=== NIFTY23200PE ===")
print(f"Spot: {spot_nifty}  Strike: {strike_n}  DTE: {dte_n}")
print(f"LTP: {ltp_pe}  Avg Buy: {avg_pe}  Qty: {qty_pe}")
print(f"IV: {iv_pe*100:.1f}%")
print(f"Delta: {g_pe['delta']:.4f}  Theta: {g_pe['theta']:.2f}/day  Gamma: {g_pe['gamma']:.6f}")
print(f"Intrinsic: {g_pe['intrinsic']}  Time Value: {float(g_pe['time_value']):.2f}")
print(f"Current PnL: {(ltp_pe - avg_pe) * qty_pe:.0f}")
print("-- Scenarios (NIFTY move) --")
for move in [-300, -200, -100, -50, 0, +50, +100]:
    new_spot = spot_nifty + move
    dp = g_pe['delta'] * move
    gp = 0.5 * g_pe['gamma'] * move**2
    th = g_pe['theta']
    new_pe = max(0.05, ltp_pe + dp + gp + th)
    pnl = (new_pe - avg_pe) * qty_pe
    print(f"  NIFTY {new_spot:.0f} ({move:+d}): PE ~{new_pe:.1f}  |  PnL {pnl:+.0f}")

print()

# ── WIPRO26JUN187.5CE ─────────────────────────────────────────────────────
spot_wipro = 184.0
strike_w   = 187.5
expiry_w   = datetime.date(2026, 6, 26)
dte_w      = max((expiry_w - today).days, 0)
ltp_ce     = 3.97
avg_ce     = 4.49
qty_ce     = 3000

iv_ce = calculate_iv(ltp_ce, spot_wipro, strike_w, dte_w, 0.065, "CE")
g_ce  = calculate_greeks(spot_wipro, strike_w, dte_w, iv_ce, 0.065, "CE")

print("=== WIPRO26JUN187.5CE ===")
print(f"Spot (est): {spot_wipro}  Strike: {strike_w}  DTE: {dte_w}")
print(f"LTP: {ltp_ce}  Avg Buy: {avg_ce}  Qty: {qty_ce}")
print(f"IV: {iv_ce*100:.1f}%")
print(f"Delta: {g_ce['delta']:.4f}  Theta: {g_ce['theta']:.2f}/day  Gamma: {g_ce['gamma']:.5f}")
print(f"Breakeven: {strike_w + avg_ce:.2f}")
print(f"Current PnL: {(ltp_ce - avg_ce) * qty_ce:.0f}")
print("-- Scenarios (WIPRO move) --")
for move in [-5, -2, 0, +2, +4, +6, +8]:
    new_spot = spot_wipro + move
    dp = g_ce['delta'] * move
    gp = 0.5 * g_ce['gamma'] * move**2
    new_ce = max(0.05, ltp_ce + dp + gp)
    pnl = (new_ce - avg_ce) * qty_ce
    print(f"  WIPRO {new_spot:.1f} ({move:+d}): CE ~{new_ce:.2f}  |  PnL {pnl:+.0f}")
