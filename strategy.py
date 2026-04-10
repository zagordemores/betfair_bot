"""
strategy.py — DCBettingStrategy

PATCH v4:
  - Edge calcolato su quote live Betfair Exchange
  - Runner DC corretti per mercati DOUBLE_CHANCE
  - placed persistito su disco (placed.json) — sopravvive ai crash
  - Check saldo disponibile prima di piazzare (solo live)
"""

import json
import logging
import os
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

PLACED_FILE = os.path.join(os.path.dirname(__file__), 'placed.json')


def _load_placed() -> set:
    """Carica il set dei market_id già scommessi dal disco."""
    try:
        if os.path.exists(PLACED_FILE):
            data = json.load(open(PLACED_FILE))
            placed = set(data.get('market_ids', []))
            logger.info(f"Placed caricato da disco: {len(placed)} mercati già scommessi")
            return placed
    except Exception as e:
        logger.error(f"Errore caricamento placed.json: {e}")
    return set()


def _save_placed(placed: set):
    """Salva il set dei market_id già scommessi su disco."""
    try:
        with open(PLACED_FILE, 'w') as f:
            json.dump({'market_ids': list(placed), 'updated': datetime.now().isoformat()}, f)
    except Exception as e:
        logger.error(f"Errore salvataggio placed.json: {e}")


class DCBettingStrategy(BaseStrategy):

    def __init__(self, value_bets: list, bankroll: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value_bets = {vb["market_id"]: vb for vb in value_bets if vb.get("market_id")}
        self.bankroll = bankroll
        self.placed = _load_placed()  # carica da disco
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
        implied_prob_betfair = 1.0 / odds_lay_live
        prob_esito_scoperto = 1.0 - prob_model
        edge_pct = (implied_prob_betfair - prob_esito_scoperto) * 100
        liab = odds_lay_live - 1.0

        if liab <= 0:
            return

        p_win  = prob_model
        p_loss = prob_esito_scoperto

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
        # Normalizza runners_map se use_match_odds=False (chiavi DC → standard)
        if not use_match_odds:
            runners_map = {
                "home": runners_map.get("home_draw"),
                "draw": runners_map.get("home_away"),
                "away": runners_map.get("away_draw"),
            }
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
            # ── LIVE: verifica saldo prima di piazzare ────────────────
            try:
                account = market._market.context.get('account_details')
                if account:
                    available = account.available_to_bet_balance
                    if available < max_liability:
                        logger.warning(
                            f"[{market_id}] Saldo insufficiente: "
                            f"disponibile €{available:.2f} < liability €{max_liability:.2f} — skip"
                        )
                        return
            except Exception:
                pass  # se non riesce a leggere il saldo, procede comunque

            # ── Trova runner e piazza ordine ──────────────────────────────
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
        _save_placed(self.placed)  # persisti su disco
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

        MATCH_ODDS (use_match_odds=True):
          runners_map = {"home": sid, "draw": sid, "away": sid}
          DC 1X → lay away, DC X2 → lay home, DC 12 → lay draw

        DOUBLE_CHANCE (use_match_odds=False):
          runners_map = {"home_draw": sid, "away_draw": sid, "home_away": sid}
          DC 1X → runner home_draw, DC X2 → runner away_draw, DC 12 → runner home_away
          La quota di questo runner è la quota lay diretta.
        """
        try:
            runner_odds = {}
            for runner in market_book.runners:
                sid = runner.selection_id
                if runner.ex.available_to_back:
                    item = runner.ex.available_to_back[0]
                    runner_odds[sid] = item['price'] if isinstance(item, dict) else item.price

            if use_match_odds:
                # MATCH_ODDS: layiamo l'esito scoperto dalla DC
                lay_runner_map = {
                    "1X": runners_map.get("away"),
                    "X2": runners_map.get("home"),
                    "12": runners_map.get("draw"),
                }
                lay_id = lay_runner_map.get(dc_type)
                if lay_id is None:
                    logger.debug(f"Runner lay non trovato per DC {dc_type}: {runners_map}")
                    return None
                return runner_odds.get(lay_id)

            else:
                # DOUBLE_CHANCE diretto: layiamo il runner DC corrispondente
                dc_runner_map = {
                    "1X": runners_map.get("home_draw"),
                    "X2": runners_map.get("away_draw"),
                    "12": runners_map.get("home_away"),
                }
                lay_id = dc_runner_map.get(dc_type)
                if lay_id is None:
                    logger.debug(f"Runner DC non trovato per DC {dc_type}: {runners_map}")
                    return None
                return runner_odds.get(lay_id)

        except Exception as e:
            logger.error(f"Errore lettura quote live Betfair: {e}")
            return None

