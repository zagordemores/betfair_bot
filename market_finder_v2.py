"""
market_finder.py v2
Trova il market_id Betfair per una partita del DC Value Engine.

FIX v2:
  - Premier League competition_id corretto: 10932509 (era 10932)
  - Runner mapping corretto per mercati DOUBLE_CHANCE
    I runner DC si chiamano "Home or Draw", "Away or Draw", "Home or Away"
    NON i nomi delle squadre — il vecchio fuzzy match falliva sempre
  - Aggiunto logging debug per i runner trovati
"""

import betfairlightweight
from betfairlightweight.filters import market_filter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)

# ─── MAPPA NOMI: DC Value Engine → Betfair ──────────────────────────────────
TEAM_NAME_MAP = {
    # Serie A
    "inter":        "internazionale",
    "milan":        "ac milan",
    "roma":         "as roma",
    "lazio":        "ss lazio",
    "napoli":       "ssc napoli",
    "fiorentina":   "acf fiorentina",
    "verona":       "hellas verona",
    "ac pisa":      "pisa",
    "como 1907":    "como",
    # Premier League
    "man city":     "manchester city",
    "man united":   "manchester united",
    "brighton hove": "brighton",
    "nottingham":   "nottingham forest",
    "leeds united": "leeds",
    # La Liga
    "atletico":     "atletico madrid",
    "barça":        "barcelona",
    # Champions League
    "psv":          "psv eindhoven",
    "leverkusen":   "bayer leverkusen",
    "dortmund":     "borussia dortmund",
    "rb leipzig":   "rb leipzig",
    "sporting cp":  "sporting",
}

# ─── MAPPA CAMPIONATI ────────────────────────────────────────────────────────
LEAGUE_MAP = {
    "serie_a":           {"event_type": "1", "competition_id": "81",       "country": "IT"},
    "premier_league":    {"event_type": "1", "competition_id": "10932509", "country": "GB"},  # FIX: era 10932
    "la_liga":           {"event_type": "1", "competition_id": "117",      "country": "ES"},
    "champions_league":  {"event_type": "1", "competition_id": "228",      "country": None},
    "europa_league":     {"event_type": "1", "competition_id": "2005",     "country": None},
    "conference_league": {"event_type": "1", "competition_id": "12375833", "country": None},  # FIX: era 6422
}


def normalize(name: str) -> str:
    name = name.lower().strip()
    for prefix in ["fc ", "ac ", "as ", "ss ", "ssc ", "afc ", "cf "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return TEAM_NAME_MAP.get(name, name)


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _extract_runners(best_match, home: str, away: str, market_type: str) -> dict:
    """
    Estrae i selection_id dei runner in base al tipo di mercato.

    MATCH_ODDS: runner si chiamano come le squadre + "The Draw"
    DOUBLE_CHANCE: runner si chiamano "Home or Draw", "Away or Draw", "Home or Away"
    """
    runners = {}
    home_norm = normalize(home)
    away_norm = normalize(away)

    logger.debug(f"Estrazione runner per mercato {market_type}:")

    for runner in best_match.runners:
        name = runner.runner_name.lower().strip()
        sid  = runner.selection_id
        logger.debug(f"  Runner: '{runner.runner_name}' (id={sid})")

        if market_type == "DOUBLE_CHANCE":
            # Runner DC: "Home or Draw", "Away or Draw", "Home or Away"
            # Betfair.it può usare "Home/Draw" o "1X" o varianti — gestiamo tutti
            if any(x in name for x in ["home or draw", "1x", "home/draw", "casa o pareggio", "1 x"]):
                runners["home_draw"] = sid
            elif any(x in name for x in ["away or draw", "x2", "away/draw", "trasferta o pareggio", "x 2"]):
                runners["away_draw"] = sid
            elif any(x in name for x in ["home or away", "12", "home/away", "casa o trasferta", "1 2"]):
                runners["home_away"] = sid
            else:
                logger.debug(f"  Runner DC non classificato: '{runner.runner_name}'")

        else:  # MATCH_ODDS
            if "draw" in name or "pareggio" in name or name == "x":
                runners["draw"] = sid
            elif similarity(home_norm, name) > 0.65:
                runners["home"] = sid
            elif similarity(away_norm, name) > 0.65:
                runners["away"] = sid
            else:
                logger.debug(f"  Runner MATCH_ODDS non classificato: '{runner.runner_name}'")

    logger.debug(f"  Runners estratti: {runners}")
    return runners


def find_market(
    trading: betfairlightweight.APIClient,
    home: str,
    away: str,
    league: str,
    match_date: str,
    market_type: str = "MATCH_ODDS",
    hours_window: int = 36,
    min_similarity: float = 0.6,
) -> dict:
    """
    Trova il market_id Betfair per una partita specifica.
    Restituisce dict con market_id, market_name, runners, use_match_odds.
    """
    league_info = LEAGUE_MAP.get(league)
    if not league_info:
        logger.error(f"Campionato non mappato: {league}")
        return None

    try:
        match_dt = datetime.strptime(match_date, "%Y-%m-%d")
    except ValueError:
        logger.error(f"Data non valida: {match_date}")
        return None

    from_dt = match_dt - timedelta(hours=2)
    to_dt   = match_dt + timedelta(hours=hours_window)

    filtro = market_filter(
        event_type_ids=[league_info["event_type"]],
        competition_ids=[league_info["competition_id"]],
        market_type_codes=[market_type],
        market_start_time={
            "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to":   to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    if league_info["country"]:
        filtro["marketCountries"] = [league_info["country"]]

    try:
        catalogo = trading.betting.list_market_catalogue(
            filter=filtro,
            market_projection=["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"],
            max_results=50,
        )
    except Exception as e:
        logger.error(f"Errore list_market_catalogue: {e}")
        return None

    if not catalogo:
        logger.warning(f"Nessun mercato trovato per {home} vs {away} ({league})")
        return None

    home_norm = normalize(home)
    away_norm = normalize(away)

    best_match = None
    best_score = 0.0

    for market in catalogo:
        event_name = market.event.name if market.event else ""
        parts = event_name.replace(" v ", " vs ").split(" vs ")
        if len(parts) != 2:
            continue

        mkt_home = normalize(parts[0].strip())
        mkt_away = normalize(parts[1].strip())

        score_h = similarity(home_norm, mkt_home)
        score_a = similarity(away_norm, mkt_away)
        score   = (score_h + score_a) / 2

        logger.debug(f"  '{home}' vs '{away}' ↔ '{parts[0]}' vs '{parts[1]}' → {score:.2f}")

        if score > best_score:
            best_score = score
            best_match = market

    if best_score < min_similarity or best_match is None:
        logger.warning(
            f"Match non trovato per {home} vs {away} "
            f"(miglior score: {best_score:.2f}, soglia: {min_similarity})"
        )
        return None

    # Estrai runner con logica corretta per tipo mercato
    runners = _extract_runners(best_match, home, away, market_type)

    result = {
        "market_id":   best_match.market_id,
        "market_name": best_match.market_name,
        "start_time":  best_match.market_start_time,
        "score":       best_score,
        "runners":     runners,
    }

    logger.info(
        f"Mercato trovato: {best_match.market_name} "
        f"[{best_match.market_id}] score={best_score:.2f} runners={runners}"
    )
    return result


def find_dc_market(
    trading: betfairlightweight.APIClient,
    home: str,
    away: str,
    league: str,
    match_date: str,
) -> dict:
    """
    Cerca prima il mercato DOUBLE_CHANCE, poi fallback su MATCH_ODDS.
    Restituisce dict con use_match_odds=False (DC diretto) o True (MATCH_ODDS).
    """
    # Prova DC diretto
    dc_market = find_market(
        trading, home, away, league, match_date,
        market_type="DOUBLE_CHANCE"
    )
    if dc_market:
        dc_market["use_match_odds"] = False
        return dc_market

    # Fallback MATCH_ODDS
    mo_market = find_market(
        trading, home, away, league, match_date,
        market_type="MATCH_ODDS"
    )
    if mo_market:
        mo_market["use_match_odds"] = True
        logger.info(f"DC non disponibile per {home} vs {away} — uso MATCH_ODDS")
        return mo_market

    return None


if __name__ == "__main__":
    from auth import get_session
    from config import BETFAIR_CONFIG
    logging.basicConfig(level=logging.DEBUG)

    trading = betfairlightweight.APIClient(
        username=BETFAIR_CONFIG['username'],
        password=BETFAIR_CONFIG['password'],
        app_key=BETFAIR_CONFIG['app_key']
    )
    trading.session_token = get_session()

    # Test con partita reale questa settimana
    res = find_dc_market(trading, "Torino", "Verona", "serie_a", "2026-04-11")
    if res:
        print(f"Trovato: {res['market_name']} [{res['market_id']}]")
        print(f"Runners: {res['runners']}")
        print(f"use_match_odds: {res['use_match_odds']}")
    else:
        print("Non trovato")
