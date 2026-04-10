"""
export_value_bets.py
Genera value_bets.json per il bot Flumine leggendo dati dal proxy locale.

Uso:
    python3 export_value_bets.py                          # serie_a, min-edge 5%
    python3 export_value_bets.py --league premier_league
    python3 export_value_bets.py --all-leagues            # tutti i campionati
    python3 export_value_bets.py --min-edge 7
"""

import argparse
import json
import math
import requests
from datetime import datetime, timedelta

PROXY_BASE  = "http://localhost:8090"
FD_KEY      = "04e0d48390734ea5808ce441ce8a6f5b"
ODDS_KEY    = "5eded5c1f6183a7c9877bafc05580665"
OUTPUT_FILE = "/home/opc/betfair_bot/value_bets.json"

BETFAIR_COMMISSION = 0.05  # 5% — deve matchare kelly_sizer.py

LEAGUE_CONFIG = {
    "serie_a":           {"fd": "SA",  "odds": "soccer_italy_serie_a"},
    "premier_league":    {"fd": "PL",  "odds": "soccer_epl"},
    "la_liga":           {"fd": "PD",  "odds": "soccer_spain_la_liga"},
    "champions_league":  {"fd": "CL",  "odds": "soccer_uefa_champs_league"},
    "europa_league":     {"fd": "EL",  "odds": "soccer_uefa_europa_league"},
    "conference_league": {"fd": "ECL", "odds": "soccer_uefa_europa_conference_league"},
}

HOME_ADV = 1.28
RHO      = -0.13


# ─── DIXON-COLES ────────────────────────────────────────────────────────────

def poisson_pmf(k, lam):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def tau(x, y, lam, mu, rho):
    if x == 0 and y == 0: return 1 - lam * mu * rho
    if x == 0 and y == 1: return 1 + lam * rho
    if x == 1 and y == 0: return 1 + mu * rho
    if x == 1 and y == 1: return 1 - rho
    return 1.0

def predict_1x2(att_h, def_h, att_a, def_a, avg_h, avg_a):
    lam = avg_h * HOME_ADV * att_h * def_a
    mu  = avg_a * att_a * def_h
    p1 = px = p2 = 0.0
    for i in range(8):
        for j in range(8):
            p = poisson_pmf(i, lam) * poisson_pmf(j, mu)
            if i <= 1 and j <= 1:
                p *= tau(i, j, lam, mu, RHO)
            if i > j:    p1 += p
            elif i == j: px += p
            else:        p2 += p
    return p1, px, p2


# ─── KELLY LAY (formula corretta) ───────────────────────────────────────────

def kelly_lay_edge(prob_dc, odds_lay, commission=BETFAIR_COMMISSION):
    """
    Calcola edge e Kelly per un LAY sull'esito scoperto.

    prob_dc  = probabilità che la DC si verifichi (dal modello)
    odds_lay = quota del runner che layiamo (l'esito NON coperto dalla DC)

    Vince il LAY se prob_dc si verifica (home/draw/away coperto esce).
    Perde il LAY se l'esito scoperto esce.

    Edge = p_win * b_net - p_loss * liability
         = prob_dc * (1 - commission) - (1 - prob_dc) * (odds_lay - 1)
    """
    p_win  = prob_dc
    p_loss = 1.0 - prob_dc
    b_net  = 1.0 * (1.0 - commission)
    liab   = odds_lay - 1.0

    if liab <= 0:
        return None, None

    edge_abs = p_win * b_net - p_loss * liab
    edge_pct = (edge_abs / liab) * 100

    # Kelly LAY
    num = p_win * b_net - p_loss * liab
    den = b_net * liab
    kelly_full = num / den if den > 0 else 0

    return round(edge_pct, 1), round(max(0, kelly_full) * 100, 2)


# ─── CALIBRAZIONE PARAMETRI ─────────────────────────────────────────────────

def calibrate(hist_data):
    teams = {}
    goals_h = goals_a = n = 0
    for m in hist_data:
        gh = m["score"]["fullTime"]["home"]
        ga = m["score"]["fullTime"]["away"]
        if gh is None or ga is None:
            continue
        h = m["homeTeam"]["shortName"]
        a = m["awayTeam"]["shortName"]
        for t in [h, a]:
            if t not in teams:
                teams[t] = {"hwGF": 0, "hwGA": 0, "hwN": 0,
                            "awGF": 0, "awGA": 0, "awN": 0}
        teams[h]["hwGF"] += gh; teams[h]["hwGA"] += ga; teams[h]["hwN"] += 1
        teams[a]["awGF"] += ga; teams[a]["awGA"] += gh; teams[a]["awN"] += 1
        goals_h += gh; goals_a += ga; n += 1

    if n == 0:
        return None, None, None

    avg_h = goals_h / n
    avg_a = goals_a / n

    params = {}
    for t, s in teams.items():
        if s["hwN"] < 3 or s["awN"] < 3:
            continue
        params[t] = {
            "att": ((s["hwGF"] / s["hwN"]) / avg_h / HOME_ADV +
                    (s["awGF"] / s["awN"]) / avg_a) / 2,
            "def": ((s["hwGA"] / s["hwN"]) / avg_a +
                    (s["awGA"] / s["awN"]) / avg_h / HOME_ADV) / 2,
        }

    return params, avg_h, avg_a


# ─── FETCH QUOTE ────────────────────────────────────────────────────────────

def fetch_odds(cfg):
    try:
        resp = requests.get(
            f"{PROXY_BASE}/odds/v4/sports/{cfg['odds']}/odds"
            f"?apiKey={ODDS_KEY}&regions=eu&markets=h2h&oddsFormat=decimal",
            timeout=10
        )
        odds_data = resp.json()
    except Exception as e:
        print(f"  [WARN] Quote non disponibili: {e}")
        return {}

    odds_map = {}
    for ev in odds_data:
        best1 = bestX = best2 = 0
        for bk in ev.get("bookmakers", []):
            h2h = next((m for m in bk.get("markets", []) if m["key"] == "h2h"), None)
            if not h2h:
                continue
            for o in h2h.get("outcomes", []):
                if o["name"] == ev["home_team"]:   best1 = max(best1, o["price"])
                elif o["name"] == ev["away_team"]: best2 = max(best2, o["price"])
                else:                               bestX = max(bestX, o["price"])
        key = ev["home_team"].lower()
        odds_map[key] = {
            "1": best1, "X": bestX, "2": best2,
            "home": ev["home_team"], "away": ev["away_team"]
        }
    return odds_map


# ─── PROCESSA UN CAMPIONATO ──────────────────────────────────────────────────

def process_league(league: str, min_edge: float, days_ahead: int = 10) -> list:
    cfg = LEAGUE_CONFIG[league]
    print(f"\n{'='*50}")
    print(f"Campionato: {league.upper()}")
    print(f"{'='*50}")

    # Storico partite
    try:
        hist_resp = requests.get(
            f"{PROXY_BASE}/fd/v4/competitions/{cfg['fd']}/matches"
            f"?status=FINISHED&limit=80",
            headers={"x-fd-key": FD_KEY},
            timeout=10
        )
        hist_data = hist_resp.json().get("matches", [])
        print(f"  Storico: {len(hist_data)} partite")
    except Exception as e:
        print(f"  [ERRORE] Storico non disponibile: {e}")
        return []

    params, avg_h, avg_a = calibrate(hist_data)
    if params is None:
        print("  [ERRORE] Calibrazione fallita")
        return []
    print(f"  Squadre calibrate: {len(params)}")

    # Fixture schedulati
    try:
        sched_resp = requests.get(
            f"{PROXY_BASE}/fd/v4/competitions/{cfg['fd']}/matches"
            f"?status=SCHEDULED",
            headers={"x-fd-key": FD_KEY},
            timeout=10
        )
        fixtures = sched_resp.json().get("matches", [])[:20]
        print(f"  Fixture in programma: {len(fixtures)}")
    except Exception as e:
        print(f"  [ERRORE] Fixture non disponibili: {e}")
        return []

    # Quote
    odds_map = fetch_odds(cfg)
    print(f"  Quote disponibili: {len(odds_map)} partite")

    # Genera value bets
    value_bets = []
    cutoff = (datetime.utcnow() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')

    for f in fixtures:
        if f['utcDate'][:10] > cutoff:
            continue

        home = f["homeTeam"]["shortName"]
        away = f["awayTeam"]["shortName"]
        date = f["utcDate"][:10]

        if home not in params or away not in params:
            continue

        p1, px, p2 = predict_1x2(
            params[home]["att"], params[home]["def"],
            params[away]["att"], params[away]["def"],
            avg_h, avg_a
        )

        # Cerca le quote
        ok = odds_map.get(home.lower()) or odds_map.get(away.lower())
        if not ok:
            continue

        o1 = ok["1"]; oX = ok["X"]; o2 = ok["2"]
        if not all([o1, oX, o2]):
            continue

        # Le tre DC: prob e quota del runner che layiamo
        # DC 1X (lay away): prob_dc=p1+px, odds_lay=o2
        # DC X2 (lay home): prob_dc=px+p2, odds_lay=o1
        # DC 12 (lay draw): prob_dc=p1+p2, odds_lay=oX
        dc_scenarios = [
            ("1X", p1 + px, o2),   # layiamo away
            ("X2", px + p2, o1),   # layiamo home
            ("12", p1 + p2, oX),   # layiamo draw
        ]

        for dc_type, prob_dc, odds_lay in dc_scenarios:
            if odds_lay <= 1.30:   # sotto 1.30 la liability è sproporzionata
                continue

            edge_pct, kelly_pct = kelly_lay_edge(prob_dc, odds_lay)

            if edge_pct is None or edge_pct < min_edge:
                continue

            # min_odds: accetta quota fino al -3% rispetto alla corrente
            min_odds_accept = round(odds_lay * 0.97, 2)

            value_bets.append({
                "home":       home,
                "away":       away,
                "league":     league,
                "date":       date,
                "dc_type":    dc_type,
                "prob_model": round(prob_dc, 4),
                "odds_lay":   round(odds_lay, 2),    # quota corrente del runner layato
                "min_odds":   min_odds_accept,        # soglia minima accettata
                "edge_pct":   edge_pct,
                "kelly_pct":  kelly_pct,
                "runners":    {},   # riempito da market_finder via bot_manager.py
                "market_id":  None, # riempito da market_finder via bot_manager.py
            })

            print(f"  ✓ {home} vs {away} | DC {dc_type} | "
                  f"lay @{odds_lay:.2f} | prob {prob_dc:.0%} | "
                  f"edge {edge_pct:.1f}% | kelly {kelly_pct:.1f}%")

    if not value_bets:
        print("  Nessuna value bet trovata con i parametri attuali")

    return value_bets


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league",      default="serie_a",
                        choices=list(LEAGUE_CONFIG.keys()))
    parser.add_argument("--all-leagues", action="store_true",
                        help="Processa tutti i campionati")
    parser.add_argument("--min-edge",    type=float, default=5.0,
                        help="Edge minimo netto dopo commissione (default 5%%)")
    parser.add_argument("--days-ahead",  type=int, default=10,
                        help="Finestra temporale in giorni (default 10)")
    args = parser.parse_args()

    leagues = list(LEAGUE_CONFIG.keys()) if args.all_leagues else [args.league]

    print(f"Export value bets | min_edge={args.min_edge}% | "
          f"days_ahead={args.days_ahead} | campionati={leagues}")

    all_bets = []
    for league in leagues:
        bets = process_league(league, args.min_edge, args.days_ahead)
        all_bets.extend(bets)

    # Salva come lista diretta (compatibile con bot_manager.py)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_bets, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"TOTALE: {len(all_bets)} value bets → {OUTPUT_FILE}")
    print(f"{'='*50}")

    if all_bets:
        print("\nRiepilogo per campionato:")
        from collections import Counter
        for league, count in Counter(b["league"] for b in all_bets).items():
            print(f"  {league}: {count} bets")
        print("\nProssimi passi:")
        print("  python3 bot_manager.py --dry-run")


if __name__ == "__main__":
    main()
