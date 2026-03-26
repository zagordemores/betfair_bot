"""
bot_manager.py — punto di ingresso del bot Betfair
Carica i value bet dal JSON, trova i market_id, avvia Flumine.

Uso:
    python bot_manager.py              # modalità normale
    python bot_manager.py --dry-run    # forza dry run
"""

import json
import logging
import sys
import time
from datetime import datetime

import betfairlightweight
from betfairlightweight.filters import streaming_market_filter
from flumine import Flumine, clients

from config import (
    USERNAME, PASSWORD, APP_KEY,
    DRY_RUN, BANKROLL, VALUE_BETS_JSON
)
from market_finder import find_dc_market
from strategy import DCBettingStrategy
from logger import notify_startup, notify_error, daily_summary

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/opc/betfair_bot/bot.log"),
    ]
)
logger = logging.getLogger(__name__)


def load_value_bets(path: str) -> list:
    """Carica value_bets.json generato dal DC Value Engine."""
    try:
        with open(path) as f:
            data = json.load(f)
        bets = data if isinstance(data, list) else data.get("bets", [])
        logger.info(f"Caricate {len(bets)} value bets da {path}")
        return bets
    except FileNotFoundError:
        logger.error(f"File non trovato: {path}")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"JSON non valido: {e}")
        return []


def enrich_with_market_ids(
    trading: betfairlightweight.APIClient,
    value_bets: list
) -> list:
    """
    Per ogni value bet, trova il market_id Betfair corrispondente.
    Filtra le scommesse per cui il mercato non viene trovato.
    """
    enriched = []
    for vb in value_bets:
        home   = vb.get("home", "")
        away   = vb.get("away", "")
        league = vb.get("league", "serie_a")
        date   = vb.get("date", "")

        logger.info(f"Cercando mercato per: {home} vs {away} ({league}) {date}")

        market_info = find_dc_market(trading, home, away, league, date)

        if market_info is None:
            logger.warning(f"Mercato non trovato per {home} vs {away}, skip")
            continue

        vb["market_id"]      = market_info["market_id"]
        vb["market_name"]    = market_info["market_name"]
        vb["runners"]        = market_info["runners"]
        vb["use_match_odds"] = market_info["use_match_odds"]

        logger.info(
            f"OK: {home} vs {away} → {market_info['market_id']} "
            f"(score={market_info['score']:.2f})"
        )
        enriched.append(vb)
        time.sleep(0.5)  # rate limiting API

    logger.info(f"Mercati trovati: {len(enriched)}/{len(value_bets)}")
    return enriched


def run_bot(dry_run: bool = DRY_RUN):
    """Avvia il bot Flumine."""
    logger.info(f"=== BOT AVVIATO {'[DRY RUN]' if dry_run else '[LIVE]'} ===")
    logger.info(f"Bankroll: €{BANKROLL:.2f}")

    # ─── LOGIN BETFAIR ───────────────────────────────────────────────────────
    trading = betfairlightweight.APIClient(
        username=USERNAME,
        password=PASSWORD,
        app_key=APP_KEY,
        locale="italian",
    )
    try:
        trading.login()
        logger.info("Login Betfair OK")
    except Exception as e:
        logger.critical(f"Login fallito: {e}")
        notify_error(f"Login Betfair fallito: {e}")
        sys.exit(1)

    # ─── CARICA VALUE BETS ───────────────────────────────────────────────────
    raw_bets = load_value_bets(VALUE_BETS_JSON)
    if not raw_bets:
        logger.warning("Nessuna value bet caricata, uscita")
        trading.logout()
        sys.exit(0)

    # ─── TROVA MARKET IDs ────────────────────────────────────────────────────
    value_bets = enrich_with_market_ids(trading, raw_bets)
    if not value_bets:
        logger.warning("Nessun mercato trovato su Betfair, uscita")
        trading.logout()
        sys.exit(0)

    market_ids = [vb["market_id"] for vb in value_bets]
    logger.info(f"Monitoraggio {len(market_ids)} mercati: {market_ids}")

    # ─── NOTIFICA AVVIO ──────────────────────────────────────────────────────
    notify_startup(dry_run, len(value_bets))

    # ─── SETUP FLUMINE ───────────────────────────────────────────────────────
    if dry_run:
        client = clients.SimulatedBetfairClient(trading)
    else:
        client = clients.BetfairClient(trading)

    framework = Flumine(client=client)

    strategy = DCBettingStrategy(
        value_bets=value_bets,
        bankroll=BANKROLL,
        market_filter=streaming_market_filter(market_ids=market_ids),
        max_order_validation_time=5.0,
        max_trade_count=1,         # max 1 scommessa per mercato
        max_live_trade_count=1,
    )

    framework.add_strategy(strategy)

    # ─── AVVIA ───────────────────────────────────────────────────────────────
    logger.info("Flumine in avvio, in ascolto sui mercati...")
    try:
        framework.run()
    except KeyboardInterrupt:
        logger.info("Bot fermato manualmente")
    except Exception as e:
        logger.error(f"Errore Flumine: {e}")
        notify_error(f"Errore Flumine: {e}")
    finally:
        trading.logout()
        logger.info("Logout OK")
        daily_summary()


if __name__ == "__main__":
    force_dry = "--dry-run" in sys.argv
    run_bot(dry_run=force_dry or DRY_RUN)
