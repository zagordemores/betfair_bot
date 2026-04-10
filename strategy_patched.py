"""
strategy.py — DCBettingStrategy

PATCH v2:
  - Rimosso trade.place_order() — causava doppio ordine in modalità live
  - Commissione inclusa nel log P&L simulato (dry run)
  - kelly_stake() chiamato con bet_type="LAY" esplicito
  - Aggiunto log della max_liability per monitoraggio rischio
"""

import logging
from datetime import datetime, timezone

from flumine import BaseStrategy
from flumine.markets.market import Market
from betfairlightweight.resources import MarketBook
from flumine.order.trade import Trade
from flumine.order.order import LimitOrder, OrderStatus

from kelly_sizer import kelly_stake, dc_to_runner_bets, BETFAIR_COMMISSION
from logger import log_bet, notify_bet_placed
from config import DRY_RUN, MIN_EDGE, MIN_ODDS, MAX_ODDS

logger = logging.getLogger(__name__)


class DCBettingStrategy(BaseStrategy):
    """
    Strategia di betting su Doppia Chance derivata dal Dixon-Coles model.

    Per ogni mercato monitorato:
    1. Legge la value bet corrispondente (caricata da value_bets.json)
    2. Controlla le quote live in streaming
    3. Se la quota >= min_odds richiesta, piazza LAY con Kelly stake corretto
    4. Non piazza mai due volte sulla stessa partita
    5. Tiene traccia della max_liability per risk management
    """

    def __init__(self, value_bets: list, bankroll: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value_bets = {vb["market_id"]: vb for vb in value_bets if vb.get("market_id")}
        self.bankroll = bankroll
        self.placed = set()
        self.total_liability = 0.0  # tracker liability totale aperta

        logger.info(f"DCBettingStrategy init: {len(self.value_bets)} mercati monitorati")

    def check_market_book(self, market: Market, market_book: MarketBook) -> bool:
        """Chiamato da Flumine ad ogni aggiornamento. Ritorna True se processare."""
        if market_book.market_id not in self.value_bets:
            return False
        if market_book.market_id in self.placed:
            return False
        if market_book.status != "OPEN":
            return False
        if market_book.inplay:
            return False
        return True

    def process_market_book(self, market: Market, market_book: MarketBook):
        """Logica principale: controlla quote e piazza LAY se edge valido."""
        market_id = market_book.market_id
        vb = self.value_bets[market_id]

        dc_type = vb["dc_type"]
        prob_model = vb["prob_model"]
        min_odds_req = vb["min_odds"]
        runners_map = vb["runners"]
        use_match_odds = vb.get("use_match_odds", True)

        # Calcola quota DC dalle quote live
        current_odds = self._get_dc_odds(market_book, dc_type, runners_map, use_match_odds)
        if current_odds is None:
            logger.debug(f"[{market_id}] Quote DC non disponibili")
            return

        if current_odds < min_odds_req:
            logger.debug(
                f"[{market_id}] Quota {current_odds:.2f} < minima {min_odds_req:.2f}, skip"
            )
            return

        # ── KELLY STAKE — formula LAY con commissione ─────────────────────
        # Nota: passiamo prob_model = prob DC (non prob lay)
        # kelly_stake gestisce internamente p_lay = 1 - prob_model
        sizing = kelly_stake(
            prob=prob_model,
            odds=current_odds,
            bankroll=self.bankroll,
            bet_type="LAY",  # ← esplicito, non default ambiguo
        )

        if not sizing["valid"]:
            logger.info(f"[{market_id}] Kelly non valido: {sizing['reason']}")
            return

        if sizing["edge"] < MIN_EDGE * 100:
            logger.info(
                f"[{market_id}] Edge netto {sizing['edge']:.1f}% < minimo {MIN_EDGE*100:.0f}%, skip"
            )
            return

        stake = sizing["stake"]
        max_liability = sizing["max_liability"]

        # Trova il runner da mettere in LAY
        dc_runners = dc_to_runner_bets(dc_type, runners_map)
        lay_id = dc_runners.get("lay")

        if lay_id is None:
            logger.error(f"[{market_id}] Runner LAY non trovato per DC {dc_type}")
            return

        # ── LOG P&L SIMULATO (include commissione) ───────────────────────
        # Se LAY vince: guadagno = stake * (1 - commission)
        # Se LAY perde: perdo = stake * (odds - 1)
        profit_if_win = round(stake * (1 - BETFAIR_COMMISSION), 2)
        loss_if_lose = round(stake * (current_odds - 1), 2)

        bet_info = {
            "home": vb["home"],
            "away": vb["away"],
            "league": vb["league"],
            "date": vb["date"],
            "dc_type": dc_type,
            "odds": current_odds,
            "prob_model": prob_model,
            "prob_lay": round(1 - prob_model, 3),
            "edge_pct": sizing["edge"],
            "kelly_pct": sizing["kelly_pct"],
            "stake": stake,
            "max_liability": max_liability,
            "profit_if_win": profit_if_win,
            "loss_if_lose": loss_if_lose,
            "commission_pct": sizing["commission_pct"],
            "market_id": market_id,
            "dry_run": DRY_RUN,
        }

        if DRY_RUN:
            logger.info(
                f"[DRY RUN] LAY {dc_type} | {vb['home']} vs {vb['away']} | "
                f"quota {current_odds:.2f} | stake €{stake:.2f} | "
                f"liability €{max_liability:.2f} | "
                f"win +€{profit_if_win:.2f} | loss -€{loss_if_lose:.2f} | "
                f"edge netto {sizing['edge']:.1f}%"
            )
            bet_info["status"] = "dry_run"
            bet_info["bet_id"] = "DRY"

        else:
            # ── LIVE: piazza ordine su Flumine ────────────────────────────
            runner = next(
                (r for r in market_book.runners if r.selection_id == lay_id), None
            )
            if runner is None:
                logger.error(f"[{market_id}] Runner {lay_id} non trovato nel market book")
                return

            if not runner.ex.available_to_back:
                logger.debug(f"[{market_id}] Nessuna liquidità back sul runner {lay_id}")
                return

            lay_price = runner.ex.available_to_back[0].price

            # Verifica liquidità sufficiente
            best_back_size = runner.ex.available_to_back[0].size
            if best_back_size < stake:
                logger.warning(
                    f"[{market_id}] Liquidità insufficiente: "
                    f"disponibile €{best_back_size:.2f} < stake €{stake:.2f}"
                )
                # Non bloccare: Betfair matcherà ciò che c'è e il resto resta in coda

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

            # ── FIX BUG #3: solo market.place_order(), NON trade.place_order() ──
            # trade.place_order() + market.place_order() causava doppio ordine.
            # Con Flumine il Trade è un contenitore logico; l'esecuzione reale
            # avviene esclusivamente tramite market.place_order().
            trade.orders.append(order)          # registra l'ordine nel trade
            market.place_order(order)            # piazza UNA SOLA VOLTA

            bet_info["status"] = "placed"
            bet_info["bet_id"] = str(order.id) if order.id else "pending"
            bet_info["lay_price"] = lay_price

            self.total_liability += max_liability
            logger.info(
                f"[LIVE] LAY piazzato | {vb['home']} vs {vb['away']} | "
                f"DC {dc_type} @{lay_price:.2f} | stake €{stake:.2f} | "
                f"liability €{max_liability:.2f} | "
                f"liability totale aperta €{self.total_liability:.2f}"
            )

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
          DC 1X = 1 / (p_home + p_draw)
          DC X2 = 1 / (p_draw + p_away)
          DC 12 = 1 / (p_home + p_away)
        dove p_X = 1 / best_back_odds_X

        Nota: la quota DC così calcolata include il margin del mercato.
        Il confronto con prob_model avviene a monte in kelly_stake.
        """
        try:
            runner_odds = {}
            for runner in market_book.runners:
                sid = runner.selection_id
                if runner.ex.available_to_back:
                    runner_odds[sid] = runner.ex.available_to_back[0].price

            home_id = runners_map.get("home")
            draw_id = runners_map.get("draw")
            away_id = runners_map.get("away")

            if not use_match_odds:
                dc_runner_map = {"1X": home_id, "X2": away_id, "12": draw_id}
                runner_id = dc_runner_map.get(dc_type)
                return runner_odds.get(runner_id)

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

            return round(1.0 / dc_prob, 2)

        except Exception as e:
            logger.error(f"Errore calcolo DC odds: {e}")
            return None
