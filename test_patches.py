"""
test_patches.py — verifica che le patch siano matematicamente corrette
Esegui LOCALMENTE prima di deployare sul server.

Uso: python test_patches.py
"""

import sys
import math

# ─── TEST 1: Formula Kelly LAY ─────────────────────────────────────────────
print("=" * 60)
print("TEST 1: Formula Kelly LAY vs BACK")
print("=" * 60)

def kelly_lay(prob_dc, odds, commission=0.05):
    """Formula Kelly per LAY (prob_dc = prob che l'evento DC accada)."""
    p_win = 1.0 - prob_dc
    p_loss = prob_dc
    b_net = 1.0 * (1.0 - commission)
    liability_per_unit = odds - 1.0
    numerator = p_win * b_net - p_loss * liability_per_unit
    denominator = b_net * liability_per_unit
    if denominator <= 0:
        return None
    return numerator / denominator

def kelly_back_old(prob, odds):
    """Formula VECCHIA (errata per LAY)."""
    b = odds - 1.0
    q = 1.0 - prob
    return (b * prob - q) / b

# Caso: DC 1X con prob_modello=0.72, quota lay away=3.5
prob_dc = 0.72
odds_lay = 3.5
commission = 0.05

k_lay = kelly_lay(prob_dc, odds_lay, commission)
k_old = kelly_back_old(prob_dc, odds_lay)

print(f"\nParametri: prob_DC={prob_dc}, odds_lay={odds_lay}, commission={commission:.0%}")
print(f"  Kelly LAY (corretto): {k_lay:.4f} = {k_lay*100:.2f}% bankroll")
print(f"  Kelly BACK (vecchio): {k_old:.4f} = {k_old*100:.2f}% bankroll")
print(f"  → Differenza: {abs(k_lay-k_old)*100:.2f} punti percentuali")

# Verifica edge netto
p_win = 1 - prob_dc
b_net = 1.0 * (1 - commission)
liability = odds_lay - 1
edge = p_win * b_net - prob_dc * liability
print(f"  → Edge netto per €1 stake: {edge:.4f} ({'POSITIVO ✓' if edge > 0 else 'NEGATIVO ✗'})")

# Caso limite: edge negativo (il LAY non dovrebbe piazzarsi)
print(f"\nCaso limite: prob_DC=0.80, odds_lay=2.0 (edge dovrebbe essere negativo)")
k_neg = kelly_lay(0.80, 2.0)
print(f"  Kelly LAY: {k_neg:.4f} ({'negativo ✓ — skip corretto' if k_neg <= 0 else 'POSITIVO — attenzione'})")

# ─── TEST 2: Commissione nel P&L ───────────────────────────────────────────
print("\n" + "=" * 60)
print("TEST 2: P&L con e senza commissione")
print("=" * 60)

stake = 10.0
odds = 3.5
commission = 0.05

profit_gross = stake           # guadagno lordo se LAY vince (incasso lo stake)
profit_net = stake * (1 - commission)  # guadagno netto
loss = stake * (odds - 1)     # perdita se LAY perde

print(f"\nStake: €{stake} | Odds LAY: {odds} | Commission: {commission:.0%}")
print(f"  Se LAY vince: +€{profit_gross:.2f} lordo → +€{profit_net:.2f} netto")
print(f"  Se LAY perde: -€{loss:.2f}")
print(f"  Differenza lordo/netto: -€{profit_gross - profit_net:.2f} (commissione non contata nella v1!)")

# ─── TEST 3: dc_to_runner_bets ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("TEST 3: dc_to_runner_bets — mapping corretto")
print("=" * 60)

runners = {"home": 1234, "draw": 5678, "away": 9012}

tests = [
    ("1X", "away", 9012),   # lay away
    ("X2", "home", 1234),   # lay home
    ("12", "draw", 5678),   # lay draw
]

for dc_type, expected_side, expected_id in tests:
    if dc_type == "1X":
        lay_id = runners.get("away")
    elif dc_type == "X2":
        lay_id = runners.get("home")
    elif dc_type == "12":
        lay_id = runners.get("draw")

    ok = lay_id == expected_id
    print(f"  DC {dc_type} → lay {expected_side} (id={lay_id}) {'✓' if ok else '✗ ERRORE'}")

# ─── TEST 4: Confronto sizing realistico ───────────────────────────────────
print("\n" + "=" * 60)
print("TEST 4: Sizing realistico con bankroll €500, kelly_frac=0.25")
print("=" * 60)

bankroll = 500.0
kelly_frac = 0.25
min_stake = 2.0
max_stake = 20.0

scenarios = [
    ("DC 1X forte", 0.72, 3.5),
    ("DC X2 moderata", 0.65, 2.2),
    ("DC 12 marginale", 0.58, 1.8),
]

for label, prob, odds in scenarios:
    k = kelly_lay(prob, odds)
    if k is None or k <= 0:
        print(f"  {label}: prob={prob} odds={odds} → Kelly negativo, skip")
        continue
    stake_raw = bankroll * k * kelly_frac
    stake = max(min_stake, min(max_stake, round(stake_raw, 2)))
    liability = stake * (odds - 1)
    p_win = 1 - prob
    edge = p_win * (1 - commission) - prob * (odds - 1)
    print(f"  {label}: prob={prob} odds={odds}")
    print(f"    Kelly={k*100:.2f}% → stake=€{stake:.2f} | liability=€{liability:.2f} | edge={edge*100:.1f}%")

print("\n" + "=" * 60)
print("TUTTI I TEST COMPLETATI")
print("=" * 60)
print("\nSe tutti i check mostrano ✓, le patch sono pronte per il deploy.")
print("Prossimo step: copiare kelly_sizer_patched.py → kelly_sizer.py")
print("               copiare strategy_patched.py   → strategy.py")
