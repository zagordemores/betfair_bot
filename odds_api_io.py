"""
odds_api_io.py — fetch quote da odds-api.io
Supporta bookmaker italiani (Eurobet IT, Sisal, Snai, ecc.)
Endpoint: https://api.odds-api.io/v1/

Differenze rispetto a The Odds API:
- Copre Eurobet IT e altri bookmaker ADM italiani
- WebSocket disponibile (qui usiamo REST per semplicità)
- Formato risposta leggermente diverso
"""

import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://api.odds-api.io/v1"

# Mappa campionati → sport slug odds-api.io
LEAGUE_SLUGS = {
    "serie_a":           "football/italy/serie-a",
    "premier_league":    "football/england/premier-league",
    "la_liga":           "football/spain/laliga",
    "champions_league":  "football/europe/champions-league",
    "europa_league":     "football/europe/europa-league",
    "conference_league": "football/europe/conference-league",
}

# Bookmaker italiani ADM da preferire
ITALIAN_BOOKMAKERS = [
    "eurobet",
    "sisal",
    "snai",
    "goldbet",
    "lottomatica",
    "betfair_it",
    "betflag",
    "bwin_it",
    "unibet_it",
    "bet365_it",
]

# Bookmaker europei affidabili come fallback
EU_BOOKMAKERS = [
    "pinnacle",
    "bet365",
    "betfair",
    "unibet",
    "bwin",
    "williamhill",
    "1xbet",
]


def fetch_odds_io(
    api_key: str,
    league: str,
    bookmakers: Optional[list] = None,
    market: str = "1x2",
) -> list:
    """
    Fetch quote da odds-api.io per un campionato.

    Args:
        api_key:     token API da odds-api.io
        league:      chiave campionato (es. "serie_a")
        bookmakers:  lista bookmaker da includere (None = tutti)
        market:      tipo mercato ("1x2", "double_chance")

    Returns:
        lista di eventi con quote nel formato standard del bot
    """
    slug = LEAGUE_SLUGS.get(league)
    if not slug:
        logger.error(f"Campionato non mappato per odds-api.io: {league}")
        return []

    params = {
        "apiKey": api_key,
        "market": market,
        "oddsFormat": "decimal",
    }
    if bookmakers:
        params["bookmakers"] = ",".join(bookmakers)

    url = f"{BASE_URL}/sports/{slug}/odds"

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.HTTPError as e:
        if resp.status_code == 401:
            logger.error("odds-api.io: API key non valida o scaduta")
        elif resp.status_code == 429:
            logger.error("odds-api.io: rate limit superato")
        else:
            logger.error(f"odds-api.io HTTP error: {e}")
        return []
    except Exception as e:
        logger.error(f"odds-api.io errore connessione: {e}")
        return []

    events = data if isinstance(data, list) else data.get("data", data.get("events", []))
    logger.info(f"odds-api.io: {len(events)} eventi trovati per {league}")
    return events


def parse_odds_io(events: list, prefer_italian: bool = True) -> dict:
    """
    Converte la risposta di odds-api.io nel formato odds_map
    usato da export_value_bets.py.

    Returns:
        dict: {home_team_norm: {"1": float, "X": float, "2": float, ...}}
    """
    odds_map = {}

    for ev in events:
        home = ev.get("home_team") or ev.get("home")
        away = ev.get("away_team") or ev.get("away")
        if not home or not away:
            continue

        bookmakers = ev.get("bookmakers", {})
        if isinstance(bookmakers, list):
            # Converti lista in dict
            bookmakers = {bk.get("key", bk.get("name", "")): bk for bk in bookmakers}

        # Ordine di preferenza: prima italiani, poi EU
        priority = (ITALIAN_BOOKMAKERS + EU_BOOKMAKERS) if prefer_italian else EU_BOOKMAKERS

        best1 = bestX = best2 = 0.0
        best1_it = bestX_it = best2_it = 0.0  # quote dai bookmaker italiani
        found_italian = False

        for bk_key, bk_data in bookmakers.items():
            bk_lower = bk_key.lower()
            is_italian = any(it in bk_lower for it in ITALIAN_BOOKMAKERS)

            markets = bk_data.get("markets", bk_data) if isinstance(bk_data, dict) else {}

            # Gestisci formato variabile
            outcomes = None
            if isinstance(markets, list):
                for m in markets:
                    if m.get("key") in ("1x2", "h2h", "moneyline"):
                        outcomes = m.get("outcomes", [])
                        break
            elif isinstance(markets, dict):
                for k in ("1x2", "h2h", "moneyline", "outcomes"):
                    if k in markets:
                        outcomes = markets[k]
                        break

            if not outcomes:
                continue

            for o in outcomes:
                name = o.get("name", "")
                price = float(o.get("price", o.get("odds", 0)) or 0)
                if price < 1.01:
                    continue

                if name == home or name.lower() == "home":
                    best1 = max(best1, price)
                    if is_italian:
                        best1_it = max(best1_it, price)
                        found_italian = True
                elif name == away or name.lower() == "away":
                    best2 = max(best2, price)
                    if is_italian:
                        best2_it = max(best2_it, price)
                else:
                    bestX = max(bestX, price)
                    if is_italian:
                        bestX_it = max(bestX_it, price)

        if not any([best1, bestX, best2]):
            continue

        # Usa quote italiane se disponibili, altrimenti best general
        final1 = best1_it if (prefer_italian and best1_it > 0) else best1
        finalX = bestX_it if (prefer_italian and bestX_it > 0) else bestX
        final2 = best2_it if (prefer_italian and best2_it > 0) else best2

        key = home.lower().strip()
        odds_map[key] = {
            "1": final1 or None,
            "X": finalX or None,
            "2": final2 or None,
            "home": home,
            "away": away,
            "has_italian_odds": found_italian,
        }

        logger.debug(
            f"  {home} vs {away}: 1={final1:.2f} X={finalX:.2f} 2={final2:.2f} "
            f"{'[IT]' if found_italian else '[EU]'}"
        )

    return odds_map


def get_available_bookmakers(api_key: str) -> list:
    """Lista bookmaker disponibili sull'account."""
    try:
        resp = requests.get(
            f"{BASE_URL}/bookmakers",
            params={"apiKey": api_key},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        bks = data if isinstance(data, list) else data.get("data", [])
        italian = [b for b in bks if any(it in b.get("key","").lower() for it in ITALIAN_BOOKMAKERS)]
        logger.info(f"Bookmaker italiani disponibili: {[b.get('key') for b in italian]}")
        return bks
    except Exception as e:
        logger.error(f"Errore fetch bookmakers: {e}")
        return []


def test_connection(api_key: str) -> bool:
    """Verifica che la key funzioni."""
    try:
        resp = requests.get(
            f"{BASE_URL}/sports/football/italy/serie-a/odds",
            params={"apiKey": api_key, "market": "1x2", "oddsFormat": "decimal"},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            n = len(data) if isinstance(data, list) else len(data.get("data", []))
            print(f"✓ odds-api.io connesso — {n} eventi Serie A trovati")
            return True
        else:
            print(f"✗ odds-api.io errore {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"✗ odds-api.io non raggiungibile: {e}")
        return False


# ─── TEST STANDALONE ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)

    API_KEY = input("Inserisci la tua odds-api.io key: ").strip()

    print("\n1. Test connessione...")
    ok = test_connection(API_KEY)
    if not ok:
        sys.exit(1)

    print("\n2. Bookmaker italiani disponibili...")
    get_available_bookmakers(API_KEY)

    print("\n3. Fetch quote Serie A...")
    events = fetch_odds_io(API_KEY, "serie_a", bookmakers=ITALIAN_BOOKMAKERS + EU_BOOKMAKERS)
    odds_map = parse_odds_io(events, prefer_italian=True)

    print(f"\nQuote trovate per {len(odds_map)} partite:")
    for home, data in list(odds_map.items())[:5]:
        it_flag = "🇮🇹" if data["has_italian_odds"] else "🇪🇺"
        print(
            f"  {it_flag} {data['home']} vs {data['away']}: "
            f"1={data['1']:.2f} X={data['X']:.2f} 2={data['2']:.2f}"
        )
