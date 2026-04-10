"""
strategy.py — DCBettingStrategy

PATCH v3:
  - Edge calcolato su quote live Betfair Exchange (non più su The Odds API)
  - min_odds dal JSON usato solo come pre-filtro di sicurezza, non per l'edge
  - Edge reale = prob_model vs quota implicita Betfair al momento dell'esecuzione
  - Questo garantisce che lo stake e la decisione siano sempre coerenti
    con il prezzo reale a cui verrà eseguito il LAY
"""

import logging
from datetime import datetime, timezone

from flumine import BaseStrategy
from flumine.markets.market import Market
from betfairlightweight.resources import MarketBook
from flumine.order.trade import Trade
from flumine.order.order import LimitOrder

from kelly_sizer import kelly_stake, dc_to_runner_bets, BETFAIR_COMMISSION
from logger import log_bet, notify_bet_placed
from config import DRY_RUN, MIN_EDGE, MIN_ODDS, MAX_ODDS

logger = logging.getLogger(__name__)


class DCBettingStrategy(BaseStrategy):
    """
    Strategia di betting su Doppia Chance derivata dal Dixon-Coles model.

    Flusso per ogni aggiornamento di mercato:
    1. Legge prob_model dal value_bets.json (stima Dixon-Coles)
    2. Legge le quote LIVE da Betfair Exchange via streaming
    3. Calcola l'edge reale: prob_model vs quota implicita Betfair
    4. Se edge > MIN_EDGE → calcola Kelly stake → piazza LAY

    Vantaggio rispetto a v2: l'edge è calcolato sullo stesso prezzo
    a cui verrà eseguita la scommessa, non su quote di terzi.
    """

    def __init__(self, value_bets: list, bankroll: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value_bets = {vb["market_id"]: vb for vb in value_bets if vb.get("market_id")}
        self.bankroll = bankroll
        self.placed = set()
        self.total_liability = 0.0

        logger.info(f"DCBettingStrategy init: {len(self.value_bets)} mercati monitorati")

    def check_market_book(self, market: Market, market_book: MarketBook) -> bool:
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
        market_id = market_book.market_id
        vb = self.value_bets[market_id]

        dc_type    = vb["dc_type"]
        prob_model = vb["prob_model"]  # prob DC dal Dixon-Coles
        runners_map = vb.get("runners", {})
        use_match_odds = vb.get("use_match_odds", True)

        # ── STEP 1: Leggi quote live Betfair ─────────────────────────────
        # Ottieni la quota del runner che layiamo (l'esito scoperto dalla DC)
        odds_lay_live = self._get_lay_odds(market_book, dc_type, runners_map, use_match_odds)

        if odds_lay_live is None:
            logger.debug(f"[{market_id}] Quote live non disponibili")
            return

        # Filtro base sulle odds (guardrail di sicurezza)
        if odds_lay_live < MIN_ODDS or odds_lay_live > MAX_ODDS:
            logger.debug(
                f"[{market_id}] Quota lay {odds_lay_live:.2f} fuori range "
                f"[{MIN_ODDS}-{MAX_ODDS}], skip"
            )
            return

        # ── STEP 2: Calcola edge REALE su quote Betfair live ─────────────
        # Edge LAY = prob_dc * (1-commission) - prob_esito_scoperto * (odds_lay - 1)
        # dove prob_esito_scoperto = 1 - prob_dc
        #
        # Questo è l'edge effettivo che realizziamo se il LAY viene matchato
        # a odds_lay_live — non una stima basata su bookmaker terzi.
        p_win  = prob_model              # prob che la DC si verifichi (lay vince)
        p_loss = 1.0 - prob_model        # prob che il lay perda
        b_net  = 1.0 * (1.0 - BETFAIR_COMMISSION)
        liab   = odds_lay_live - 1.0

        if liab <= 0:
            return

        edge_abs = p_win * b_net - p_loss * liab
        edge_pct = (edge_abs / liab) * 100  # edge relativo alla liability

        # Quota implicita Betfair per il runner layato
        implied_prob_betfair = 1.0 / odds_lay_live

        logger.debug(
            f"[{market_id}] {vb['home']} vs {vb['away']} | DC {dc_type} | "
            f"odds_lay={odds_lay_live:.2f} | implied={implied_prob_betfair:.1%} | "
            f"prob_dc={prob_model:.1%} | edge={edge_pct:.1f}%"
        )

        if edge_pct < MIN_EDGE * 100:
            logger.debug(
                f"[{market_id}] Edge Betfair {edge_pct:.1f}% < minimo "
                f"{MIN_EDGE*100:.0f}%, skip"
            )
            return

        # ── STEP 3: Kelly stake su quote Betfair live ─────────────────────
        sizing = kelly_stake(
            prob=prob_model,
            odds=odds_lay_live,
            bankroll=self.bankroll,
            bet_type="LAY",
        )

        if not sizing["valid"]:
            logger.info(f"[{market_id}] Kelly non valido: {sizing['reason']}")
            return

        stake = sizing["stake"]
        max_liability = sizing["max_liability"]

        # ── STEP 4: Trova runner LAY ──────────────────────────────────────
        dc_runners = dc_to_runner_bets(dc_type, runners_map)
        lay_id = dc_runners.get("lay")

        if lay_id is None:
            logger.error(f"[{market_id}] Runner LAY non trovato per DC {dc_type}")
            return

        # ── STEP 5: P&L simulato ──────────────────────────────────────────
        profit_if_win = round(stake * (1.0 - BETFAIR_COMMISSION), 2)
        loss_if_lose  = round(stake * (odds_lay_live - 1.0), 2)

        bet_info = {
            "home":              vb["home"],
            "away":              vb["away"],
            "league":            vb["league"],
            "date":              vb["date"],
            "dc_type":           dc_type,
            "odds":              odds_lay_live,           # quota LIVE Betfair
            "implied_betfair":   round(implied_prob_betfair * 100, 1),
            "prob_model":        prob_model,
            "prob_lay":          round(1.0 - prob_model, 3),
            "edge_pct":          round(edge_pct, 2),      # edge su Betfair live
            "kelly_pct":         sizing["kelly_pct"],
            "stake":             stake,
            "max_liability":     max_liability,
            "profit_if_win":     profit_if_win,
            "loss_if_lose":      loss_if_lose,
            "commission_pct":    sizing["commission_pct"],
            "market_id":         market_id,
            "dry_run":           DRY_RUN,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
        }

        if DRY_RUN:
            logger.info(
                f"[DRY RUN] LAY {dc_type} | {vb['home']} vs {vb['away']} | "
                f"quota Betfair {odds_lay_live:.2f} (implied {implied_prob_betfair:.1%}) | "
                f"prob_DC {prob_model:.1%} | "
                f"edge {edge_pct:.1f}% | stake €{stake:.2f} | "
                f"liability €{max_liability:.2f} | "
                f"win +€{profit_if_win:.2f} | loss -€{loss_if_lose:.2f}"
            )
            bet_info["status"] = "dry_run"
            bet_info["bet_id"] = "DRY"

        else:
            # ── LIVE: trova runner e piazza ordine ─────────────────────
            runner = next(
                (r for r in market_book.runners if r.selection_id == lay_id), None
            )
            if runner is None:
                logger.error(f"[{market_id}] Runner {lay_id} non trovato")
                return

            if not runner.ex.available_to_back:
                logger.debug(f"[{market_id}] Nessuna liquidità back sul runner {lay_id}")
                return

            lay_price = runner.ex.available_to_back[0].price
            best_back_size = runner.ex.available_to_back[0].size

            if best_back_size < stake * 0.5:
                logger.warning(
                    f"[{market_id}] Liquidità bassa: "
                    f"disponibile €{best_back_size:.2f} vs stake €{stake:.2f}"
                )

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

            # FIX BUG #3: solo market.place_order(), mai trade.place_order()
            trade.orders.append(order)
            market.place_order(order)

            bet_info["status"] = "placed"
            bet_info["bet_id"] = str(order.id) if order.id else "pending"
            bet_info["odds"]   = lay_price  # prezzo effettivo di esecuzione

            self.total_liability += max_liability
            logger.info(
                f"[LIVE] LAY piazzato | {vb['home']} vs {vb['away']} | "
                f"DC {dc_type} @{lay_price:.2f} | stake €{stake:.2f} | "
                f"liability €{max_liability:.2f} | "
                f"liability totale €{self.total_liability:.2f}"
            )

        self.placed.add(market_id)
        log_bet(bet_info)
        notify_bet_placed(bet_info, DRY_RUN)

    def _get_lay_odds(
        self,
        market_book: MarketBook,
        dc_type: str,
        runners_map: dict,
        use_match_odds: bool,
    ) -> float:
        """
        Restituisce la quota del runner che layiamo (l'esito scoperto dalla DC).

        DC 1X → lay away  → odds del runner away
        DC X2 → lay home  → odds del runner home
        DC 12 → lay draw  → odds del runner draw

        Se use_match_odds=True (mercato MATCH_ODDS):
          usa best back del runner scoperto

        Se use_match_odds=False (mercato DOUBLE_CHANCE diretto):
          calcola la quota DC dalle tre probabilità implicite e
          la quota del runner DC corrispondente

        In entrambi i casi la quota è LIVE da Betfair streaming.
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

            if use_match_odds:
                # Quota del runner che layiamo (esito scoperto)
                lay_runner_map = {
                    "1X": away_id,   # lay away
                    "X2": home_id,   # lay home
                    "12": draw_id,   # lay draw
                }
                lay_id = lay_runner_map.get(dc_type)
                return runner_odds.get(lay_id)

            else:
                # Mercato DC diretto: calcola quota DC dalle tre prob implicite
                o_h = runner_odds.get(home_id)
                o_d = runner_odds.get(draw_id)
                o_a = runner_odds.get(away_id)

                if None in (o_h, o_d, o_a):
                    return None

                p_h = 1.0 / o_h
                p_d = 1.0 / o_d
                p_a = 1.0 / o_a

                # Quota del runner SCOPERTO (quello che layiamo)
                if dc_type == "1X":
                    # scoperto = away → quota implicita dell'away
                    return o_a
                elif dc_type == "X2":
                    return o_h
                elif dc_type == "12":
                    return o_d
                else:
                    return None

        except Exception as e:
            logger.error(f"Errore lettura quote live Betfair: {e}")
            return None
