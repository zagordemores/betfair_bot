"""
strategy.py — DCBettingStrategy
Cuore del bot: legge i value bet, monitora le quote Betfair in streaming,
e piazza le scommesse quando le condizioni sono soddisfatte.
"""

import logging
from datetime import datetime, timezone

from flumine import BaseStrategy
from flumine.markets.market import Market
from betfairlightweight.resources import MarketBook
from flumine.order.trade import Trade
from flumine.order.order import LimitOrder, OrderStatus

from kelly_sizer import kelly_stake, dc_to_runner_bets
from logger import log_bet, notify_bet_placed
from config import DRY_RUN, MIN_EDGE, MIN_ODDS, MAX_ODDS

logger = logging.getLogger(__name__)


class DCBettingStrategy(BaseStrategy):
    """
    Strategia di betting su Doppia Chance derivata dal Dixon-Coles model.

    Per ogni mercato monitorato:
    1. Legge la value bet corrispondente (caricata da value_bets.json)
    2. Controlla le quote live in streaming
    3. Se la quota >= min_odds richiesta, piazza la scommessa con Kelly stake
    4. Non piazza mai due volte sulla stessa partita
    """

    def __init__(self, value_bets: list, bankroll: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dizionario market_id → value_bet
        self.value_bets   = {vb["market_id"]: vb for vb in value_bets if vb.get("market_id")}
        self.bankroll     = bankroll
        self.placed       = set()   # market_id già scommessi
        logger.info(f"DCBettingStrategy init: {len(self.value_bets)} mercati monitorati")

    def check_market_book(self, market: Market, market_book: MarketBook) -> bool:
        """Chiamato da Flumine ad ogni aggiornamento. Ritorna True se processare."""
        # Processa solo mercati che abbiamo in lista
        if market_book.market_id not in self.value_bets:
            return False
        # Non processare mercati già scommessi
        if market_book.market_id in self.placed:
            return False
        # Non processare mercati già chiusi o in-play (se non vogliamo)
        if market_book.status != "OPEN":
            return False
        # Non processare se la partita è già iniziata
        if market_book.inplay:
            return False
        return True

    def process_market_book(self, market: Market, market_book: MarketBook):
        """Logica principale: controlla quote e piazza scommessa se valida."""
        market_id = market_book.market_id
        vb = self.value_bets[market_id]

        dc_type     = vb["dc_type"]         # "1X", "X2", "12"
        prob_model  = vb["prob_model"]      # probabilità stimata dal Dixon-Coles
        min_odds_req = vb["min_odds"]       # quota minima accettata (da DC Value Engine)
        runners_map  = vb["runners"]        # {"home": id, "draw": id, "away": id}
        use_match_odds = vb.get("use_match_odds", True)

        # Calcola la quota DC dalle quote live
        current_odds = self._get_dc_odds(market_book, dc_type, runners_map, use_match_odds)
        if current_odds is None:
            logger.debug(f"[{market_id}] Quote DC non disponibili")
            return

        # Verifica che la quota sia ancora accettabile
        if current_odds < min_odds_req:
            logger.debug(
                f"[{market_id}] Quota {current_odds:.2f} < minima {min_odds_req:.2f}, skip"
            )
            return

        # Calcola Kelly stake
        sizing = kelly_stake(
            prob=prob_model,
            odds=current_odds,
            bankroll=self.bankroll,
        )

        if not sizing["valid"]:
            logger.info(f"[{market_id}] Kelly non valido: {sizing['reason']}")
            return

        if sizing["edge"] < MIN_EDGE * 100:
            logger.info(
                f"[{market_id}] Edge {sizing['edge']:.1f}% < minimo {MIN_EDGE*100:.0f}%, skip"
            )
            return

        stake = sizing["stake"]

        # ─── PIAZZA SCOMMESSA ─────────────────────────────────────────────
        # Su Betfair Exchange, per la DC facciamo LAY sull'esito scoperto
        dc_runners = dc_to_runner_bets(dc_type, runners_map)
        lay_id = dc_runners.get("lay")

        if lay_id is None:
            logger.error(f"[{market_id}] Runner LAY non trovato per DC {dc_type}")
            return

        bet_info = {
            "home":      vb["home"],
            "away":      vb["away"],
            "league":    vb["league"],
            "date":      vb["date"],
            "dc_type":   dc_type,
            "odds":      current_odds,
            "prob_model": prob_model,
            "edge_pct":  sizing["edge"],
            "kelly_pct": sizing["kelly_pct"],
            "stake":     stake,
            "market_id": market_id,
            "dry_run":   DRY_RUN,
        }

        if DRY_RUN:
            logger.info(
                f"[DRY RUN] Piazzerei LAY {dc_type} su {vb['home']} vs {vb['away']} "
                f"@{current_odds:.2f} stake €{stake:.2f} edge {sizing['edge']:.1f}%"
            )
            bet_info["status"] = "dry_run"
            bet_info["bet_id"] = "DRY"
        else:
            # Trova il runner da lay
            runner = next(
                (r for r in market_book.runners if r.selection_id == lay_id), None
            )
            if runner is None:
                logger.error(f"[{market_id}] Runner {lay_id} non trovato nel market book")
                return

            # Lay price = best back price del runner scoperto
            if not runner.ex.available_to_back:
                logger.debug(f"[{market_id}] Nessuna liquidità back sul runner {lay_id}")
                return

            lay_price = runner.ex.available_to_back[0].price

            trade = Trade(
                market_id=market_id,
                selection_id=lay_id,
                handicap=0,
                strategy=self,
            )
            order = LimitOrder(
                side="LAY",
                size=round(stake, 2),
                price=lay_price,
            )
            trade.place_order(order)
            market.place_order(order)

            bet_info["status"] = "placed"
            bet_info["bet_id"] = str(order.id) if order.id else "pending"
            logger.info(
                f"[LIVE] LAY piazzato: {vb['home']} vs {vb['away']} "
                f"DC {dc_type} @{lay_price:.2f} €{stake:.2f}"
            )

        # Segna come piazzato e logga
        self.placed.add(market_id)
        log_bet(bet_info)
        notify_bet_placed(bet_info, DRY_RUN)

    def _get_dc_odds(
        self,
        market_book: MarketBook,
        dc_type: str,
        runners_map: dict,
        use_match_odds: bool,
    ) -> float:
        """
        Calcola la quota Doppia Chance dal market book.

        Se use_match_odds=True, deriva la DC dalle quote 1X2:
        - DC 1X = 1 / (p_home + p_draw)
        - DC X2 = 1 / (p_draw + p_away)
        - DC 12 = 1 / (p_home + p_away)

        Dove p_X = 1 / best_back_odds_X
        """
        try:
            # Raccogli best back per ogni runner
            runner_odds = {}
            for runner in market_book.runners:
                sid = runner.selection_id
                if runner.ex.available_to_back:
                    runner_odds[sid] = runner.ex.available_to_back[0].price

            home_id = runners_map.get("home")
            draw_id = runners_map.get("draw")
            away_id = runners_map.get("away")

            if not use_match_odds:
                # Mercato DC diretto — cerca la quota del runner DC
                dc_runner_map = {"1X": home_id, "X2": away_id, "12": draw_id}
                runner_id = dc_runner_map.get(dc_type)
                return runner_odds.get(runner_id)

            # Calcola DC da MATCH_ODDS
            o_h = runner_odds.get(home_id)
            o_d = runner_odds.get(draw_id)
            o_a = runner_odds.get(away_id)

            if None in (o_h, o_d, o_a):
                return None

            p_h = 1.0 / o_h
            p_d = 1.0 / o_d
            p_a = 1.0 / o_a

            if dc_type == "1X":
                dc_prob = p_h + p_d
            elif dc_type == "X2":
                dc_prob = p_d + p_a
            elif dc_type == "12":
                dc_prob = p_h + p_a
            else:
                return None

            if dc_prob <= 0:
                return None

            # Quota DC senza margin
            dc_odds = round(1.0 / dc_prob, 2)
            return dc_odds

        except Exception as e:
            logger.error(f"Errore calcolo DC odds: {e}")
            return None
