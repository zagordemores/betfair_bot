"""
market_finder.py
Trova il market_id Betfair corrispondente a una partita del DC Value Engine.

Il problema principale: il DC Value Engine usa nomi come "Inter" o "AC Pisa",
mentre Betfair usa nomi come "Internazionale" o "Pisa". Serve un fuzzy match robusto.
"""

import betfairlightweight
from betfairlightweight.filters import market_filter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)

# ─── MAPPA NOMI: DC Value Engine → Betfair ─────────────────────────────────
# Aggiungere qui le squadre che non vengono trovate automaticamente
TEAM_NAME_MAP = {
    # Serie A
    "inter":            "internazionale",
    "milan":            "ac milan",
    "roma":             "as roma",
    "lazio":            "ss lazio",
    "napoli":           "ssc napoli",
    "juventus":         "juventus",
    "atalanta":         "atalanta",
    "fiorentina":       "acf fiorentina",
    "bologna":          "bologna",
    "torino":           "torino",
    "udinese":          "udinese",
    "genoa":            "genoa",
    "cagliari":         "cagliari",
    "lecce":            "lecce",
    "verona":           "hellas verona",
    "ac pisa":          "pisa",
    "sassuolo":         "sassuolo",
    "parma":            "parma",
    "como 1907":        "como",
    "cremonese":        "cremonese",
    # Champions League
    "real madrid":      "real madrid",
    "barcelona":        "barcelona",
    "barça":            "barcelona",
    "man city":         "manchester city",
    "man united":       "manchester united",
    "atletico":         "atletico madrid",
    "psv":              "psv eindhoven",
    "leverkusen":       "bayer leverkusen",
    "dortmund":         "borussia dortmund",
    "rb leipzig":       "rb leipzig",
    "porto":            "porto",
    "benfica":          "benfica",
    "celtic":           "celtic",
    "rangers":          "rangers",
    "ajax":             "ajax",
    "psv":              "psv eindhoven",
}

# ─── MAPPA CAMPIONATI: DC Value Engine → Betfair competition_id ────────────
LEAGUE_MAP = {
    "serie_a":           {"event_type": "1", "competition_id": "81",    "country": "IT"},
    "test": {"event_type": "1", "competition_id": None, "country": None},
    "premier_league":    {"event_type": "1", "competition_id": "10932", "country": "GB"},
    "la_liga":           {"event_type": "1", "competition_id": "117",   "country": "ES"},
    "champions_league":  {"event_type": "1", "competition_id": "228",   "country": None},
    "europa_league":     {"event_type": "1", "competition_id": "2005",  "country": None},
    "conference_league": {"event_type": "1", "competition_id": "6422",  "country": None},
}


def normalize(name: str) -> str:
    """Normalizza nome squadra per il confronto."""
    name = name.lower().strip()
    # Rimuovi prefissi comuni
    for prefix in ["fc ", "ac ", "as ", "ss ", "ssc ", "afc ", "cf "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    # Applica mappa aliases
    return TEAM_NAME_MAP.get(name, name)


def similarity(a: str, b: str) -> float:
    """Calcola similarità tra due stringhe (0-1)."""
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def find_market(
    trading: betfairlightweight.APIClient,
    home: str,
    away: str,
    league: str,
    match_date: str,          # formato YYYY-MM-DD
    market_type: str = "MATCH_ODDS",
    hours_window: int = 36,   # cerca partite entro N ore dalla data
    min_similarity: float = 0.6,
) -> object:
    """
    Trova il market_id Betfair per una partita specifica.

    Returns:
        dict con market_id, market_name, runners, start_time
        oppure None se non trovato
    """
    league_info = LEAGUE_MAP.get(league)
    if not league_info:
        logger.error(f"Campionato non mappato: {league}")
        return None

    # Finestra temporale intorno alla data della partita
    try:
        match_dt = datetime.strptime(match_date, "%Y-%m-%d")
    except ValueError:
        logger.error(f"Data non valida: {match_date}")
        return None

    from_dt = match_dt - timedelta(hours=2)
    to_dt   = match_dt + timedelta(hours=hours_window)

    # Costruisci filtro
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

    # Fuzzy match sul nome dell'evento
    home_norm = normalize(home)
    away_norm = normalize(away)

    best_match = None
    best_score = 0.0

    for market in catalogo:
        event_name = market.event.name if market.event else ""
        # Betfair scrive "Home v Away" o "Home vs Away"
        parts = event_name.replace(" v ", " vs ").split(" vs ")
        if len(parts) != 2:
            continue

        mkt_home = normalize(parts[0].strip())
        mkt_away = normalize(parts[1].strip())

        score_h = similarity(home_norm, mkt_home)
        score_a = similarity(away_norm, mkt_away)
        score   = (score_h + score_a) / 2

        logger.debug(
            f"  Confronto: '{home}' vs '{away}' ↔ '{parts[0]}' vs '{parts[1]}' "
            f"→ score {score:.2f}"
        )

        if score > best_score:
            best_score = score
            best_match = market

    if best_score < min_similarity or best_match is None:
        logger.warning(
            f"Match non trovato per {home} vs {away} "
            f"(miglior score: {best_score:.2f}, soglia: {min_similarity})"
        )
        return None

    # Estrai runner IDs (casa=prima selezione, away=seconda, draw=terza)
    runners = {}
    for runner in best_match.runners:
        name = runner.runner_name.lower()
        if normalize(home) in name or similarity(home_norm, name) > 0.7:
            runners["home"] = runner.selection_id
        elif normalize(away) in name or similarity(away_norm, name) > 0.7:
            runners["away"] = runner.selection_id
        else:
            runners["draw"] = runner.selection_id

    result = {
        "market_id":   best_match.market_id,
        "market_name": best_match.market_name,
        "start_time":  best_match.market_start_time,
        "score":       best_score,
        "runners":     runners,
    }

    logger.info(
        f"Mercato trovato: {best_match.market_name} "
        f"[{best_match.market_id}] score={best_score:.2f}"
    )
    return result


def find_dc_market(
    trading: betfairlightweight.APIClient,
    home: str,
    away: str,
    league: str,
    match_date: str,
) -> object:
    """
    Trova il mercato DOUBLE_CHANCE (se disponibile) o deriva
    le quote DC dal mercato MATCH_ODDS principale.

    Returns:
        dict con market_id del DC o del MATCH_ODDS + flag use_match_odds
    """
    # Prova prima il mercato DC diretto
    dc_market = find_market(
        trading, home, away, league, match_date,
        market_type="DOUBLE_CHANCE"
    )
    if dc_market:
        dc_market["use_match_odds"] = False
        return dc_market

    # Fallback: usa MATCH_ODDS e calcola DC in codice
    mo_market = find_market(
        trading, home, away, league, match_date,
        market_type="MATCH_ODDS"
    )
    if mo_market:
        mo_market["use_match_odds"] = True
        logger.info(
            f"Mercato DC non disponibile per {home} vs {away}, "
            f"uso MATCH_ODDS con calcolo DC interno"
        )
        return mo_market

    return None


# ─── TEST STANDALONE ────────────────────────────────────────────────────────


if __name__ == "__main__":
    import os
    from auth import get_session
    from config import BETFAIR_CONFIG
    
    logging.basicConfig(level=logging.INFO)
    
    trading = betfairlightweight.APIClient(
        username=BETFAIR_CONFIG['username'],
        password=BETFAIR_CONFIG['password'],
        app_key=BETFAIR_CONFIG['app_key']
    )
    
    token = get_session()
    if token:
        trading.session_token = token
        print("✅ Test Mapping: Sessione attiva")
        # Test rapido con nomi generici
        res = find_dc_market(trading, "Italy", "Northern Ireland", "serie_a", datetime.now().strftime("%Y-%m-%d"))
        if res:
            print(f"🎯 Trovato: {res['market_name']}")
    else:
        print("❌ Login fallito")
