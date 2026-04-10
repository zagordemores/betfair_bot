"""
export_value_bets.py
Script da eseguire sul server dopo aver premuto FETCH nel DC Value Engine.
Chiama l'API del proxy e genera value_bets.json per il bot Flumine.

Uso:
    python export_value_bets.py --league serie_a --min-edge 8
"""

import argparse
import json
import requests
from datetime import datetime, timedelta, timedelta

PROXY_BASE   = "http://localhost:8090"
FD_KEY       = "04e0d48390734ea5808ce441ce8a6f5b"
ODDS_KEY     = "5eded5c1f6183a7c9877bafc05580665"
OUTPUT_FILE  = "/home/opc/betfair_bot/value_bets.json"

LEAGUE_CONFIG = {
    "serie_a":          {"fd": "SA",  "odds": "soccer_italy_serie_a"},
    "premier_league":   {"fd": "PL",  "odds": "soccer_epl"},
    "la_liga":          {"fd": "PD",  "odds": "soccer_spain_la_liga"},
    "champions_league": {"fd": "CL",  "odds": "soccer_uefa_champs_league"},
    "europa_league":    {"fd": "EL",  "odds": "soccer_uefa_europa_league"},
    "conference_league":{"fd": "ECL", "odds": "soccer_uefa_europa_conference_league"},
}

HOME_ADV = 1.28
RHO      = -0.13

import math

def poisson_pmf(k, lam):
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def tau(x, y, lam, mu, rho):
    if x==0 and y==0: return 1 - lam*mu*rho
    if x==0 and y==1: return 1 + lam*rho
    if x==1 and y==0: return 1 + mu*rho
    if x==1 and y==1: return 1 - rho
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
            if i > j:  p1 += p
            elif i == j: px += p
            else:        p2 += p
    return p1, px, p2

def calc_edge(prob_model, odds_bk):
    if not odds_bk or odds_bk <= 1:
        return None, None
    implied = 1.0 / odds_bk
    edge = (prob_model - implied) * 100
    return round(edge, 1), round(implied * 100, 1)

def main(league: str, min_edge: float, odds_source: str = "theoddsapi", odds_key: str = ""):
    cfg = LEAGUE_CONFIG[league]

    # Fetch risultati storici
    hist_resp = requests.get(
        f"{PROXY_BASE}/fd/v4/competitions/{cfg['fd']}/matches?status=FINISHED&limit=80",
        headers={"x-fd-key": FD_KEY}
    )
    hist_data = hist_resp.json().get("matches", [])

    # Calibra parametri squadre
    teams = {}
    goals_h = goals_a = n = 0
    for m in hist_data:
        gh = m["score"]["fullTime"]["home"]
        ga = m["score"]["fullTime"]["away"]
        if gh is None: continue
        h = m["homeTeam"]["shortName"]
        a = m["awayTeam"]["shortName"]
        if h not in teams: teams[h] = {"hwGF":0,"hwGA":0,"hwN":0,"awGF":0,"awGA":0,"awN":0}
        if a not in teams: teams[a] = {"hwGF":0,"hwGA":0,"hwN":0,"awGF":0,"awGA":0,"awN":0}
        teams[h]["hwGF"]+=gh; teams[h]["hwGA"]+=ga; teams[h]["hwN"]+=1
        teams[a]["awGF"]+=ga; teams[a]["awGA"]+=gh; teams[a]["awN"]+=1
        goals_h+=gh; goals_a+=ga; n+=1

    if n == 0:
        print("Nessun dato storico")
        return

    avg_h = goals_h / n
    avg_a = goals_a / n

    params = {}
    for t, s in teams.items():
        if s["hwN"] < 3 or s["awN"] < 3: continue
        params[t] = {
            "att": ((s["hwGF"]/s["hwN"])/avg_h/HOME_ADV + (s["awGF"]/s["awN"])/avg_a) / 2,
            "def": ((s["hwGA"]/s["hwN"])/avg_a + (s["awGA"]/s["awN"])/avg_h/HOME_ADV) / 2,
        }

    # Fetch fixture schedulati
    sched_resp = requests.get(
        f"{PROXY_BASE}/fd/v4/competitions/{cfg['fd']}/matches?status=SCHEDULED",
        headers={"x-fd-key": FD_KEY}
    )
    fixtures = sched_resp.json().get("matches", [])[:20]

    # Fetch quote
    if odds_source == "oddsio" and odds_key:
        from odds_api_io import fetch_odds_io, parse_odds_io, ITALIAN_BOOKMAKERS, EU_BOOKMAKERS
        events = fetch_odds_io(odds_key, league, bookmakers=ITALIAN_BOOKMAKERS + EU_BOOKMAKERS)
        odds_map = parse_odds_io(events, prefer_italian=True)
        print(f"Sorgente: odds-api.io — {len(odds_map)} partite con quote")
    else:
        odds_resp = requests.get(
            f"{PROXY_BASE}/odds/v4/sports/{cfg['odds']}/odds"
            f"?apiKey={ODDS_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
        )
        odds_data = odds_resp.json()
        odds_map = {}
        for ev in odds_data:
            best1 = bestX = best2 = 0
            for bk in ev.get("bookmakers", []):
                h2h = next((m for m in bk.get("markets",[]) if m["key"]=="h2h"), None)
                if not h2h: continue
                for o in h2h.get("outcomes", []):
                    if o["name"] == ev["home_team"]: best1 = max(best1, o["price"])
                    elif o["name"] == ev["away_team"]: best2 = max(best2, o["price"])
                    else: bestX = max(bestX, o["price"])
            odds_map[ev["home_team"].lower()] = {"1": best1, "X": bestX, "2": best2,
                                                  "home": ev["home_team"], "away": ev["away_team"]}

    # Genera value bets
    value_bets = []
    cutoff = (datetime.utcnow() + timedelta(days=10)).strftime('%Y-%m-%d')
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

        # Trova quote bookmaker
        ok = odds_map.get(home.lower()) or odds_map.get(away.lower())
        if not ok: continue

        o1 = ok["1"]; oX = ok["X"]; o2 = ok["2"]
        if not all([o1, oX, o2]): continue

        # Calcola DC
        dc_scenarios = [
            ("1X", p1+px, 1/(1/o1+1/oX) if o1 and oX else None),
            ("X2", px+p2, 1/(1/oX+1/o2) if oX and o2 else None),
            ("12", p1+p2, 1/(1/o1+1/o2) if o1 and o2 else None),
        ]

        for dc_type, prob, dc_odds in dc_scenarios:
            if dc_odds is None: continue
            edge, implied = calc_edge(prob, dc_odds)
            if edge is None or edge < min_edge: continue

            # Kelly
            b = dc_odds - 1
            q = 1 - prob
            kelly_full = (b * prob - q) / b if b > 0 else 0
            kelly_half = max(0, kelly_full * 0.5)

            value_bets.append({
                "home":       home,
                "away":       away,
                "league":     league,
                "date":       date,
                "dc_type":    dc_type,
                "prob_model": round(prob, 4),
                "min_odds":   round(dc_odds * 0.97, 2),  # accetta fino al -3% dalla quota attuale
                "edge_pct":   edge,
                "kelly_pct":  round(kelly_half * 100, 2),
                "market_id":  None,  # verrà riempito da bot_manager.py
            })

    # Salva JSON
    output = {"generated_at": datetime.now().isoformat(), "league": league, "bets": value_bets}
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Value bets esportate: {len(value_bets)} → {OUTPUT_FILE}")
    for vb in value_bets:
        print(f"  {vb['home']} vs {vb['away']} | {vb['dc_type']} | edge {vb['edge_pct']}% | kelly {vb['kelly_pct']}%")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league",       default="serie_a")
    parser.add_argument("--min-edge",     type=float, default=8.0)
    parser.add_argument("--odds-source",  default="theoddsapi", choices=["theoddsapi", "oddsio"])
    parser.add_argument("--odds-key",     default="")
    args = parser.parse_args()
    main(args.league, args.min_edge, args.odds_source, args.odds_key)
