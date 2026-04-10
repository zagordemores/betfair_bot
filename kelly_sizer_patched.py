"""
kelly_sizer.py — calcola stake ottimale da Kelly Criterion

PATCH v2:
  - Formula Kelly corretta per scommesse LAY (non BACK)
  - Commissione Betfair inclusa nel calcolo (default 5%)
  - Edge calcolato sul profitto NETTO dopo commissione
"""

import logging
from config import BANKROLL, MAX_KELLY_FRAC, MIN_STAKE, MAX_STAKE, MIN_ODDS, MAX_ODDS

logger = logging.getLogger(__name__)

# Commissione Betfair.it sul profitto netto di ogni mercato
BETFAIR_COMMISSION = 0.05  # 5%


def kelly_stake(
    prob: float,
    odds: float,
    bankroll: float = BANKROLL,
    kelly_frac: float = MAX_KELLY_FRAC,
    min_stake: float = MIN_STAKE,
    max_stake: float = MAX_STAKE,
    commission: float = BETFAIR_COMMISSION,
    bet_type: str = "LAY",  # "LAY" o "BACK"
) -> dict:
    """
    Calcola la puntata ottimale secondo il Criterio di Kelly.

    IMPORTANTE: prob è la probabilità che l'evento DC si verifichi.
    Per un LAY, stiamo puntando che l'evento NON si verifichi.

    Per LAY DC (es. lay sull'away per coprire DC 1X):
        - prob_lay = 1 - prob_dc  (probabilità che il lay vinca)
        - b = (odds - 1) * (1 - commission)  (guadagno netto se il lay vince)
        - liability = odds - 1  (rischio se il lay perde)
        - Kelly LAY: f = (p_lay * b - p_loss) / b
          dove p_loss = prob_dc (prob che il lay perda)

    Args:
        prob:       probabilità che l'evento DC si verifichi (dal Dixon-Coles)
        odds:       quota decimale del runner che mettiamo in LAY
        bankroll:   capitale totale disponibile
        kelly_frac: frazione di Kelly da usare (0.25 consigliato per LAY)
        commission: commissione Betfair sul profitto (default 5%)
        bet_type:   "LAY" (default) o "BACK"

    Returns:
        dict con stake, kelly_pct, edge_net, valid
    """

    if odds < MIN_ODDS or odds > MAX_ODDS:
        return {
            "valid": False,
            "reason": f"Quota {odds:.2f} fuori range [{MIN_ODDS}-{MAX_ODDS}]",
            "stake": 0.0,
            "kelly_pct": 0.0,
            "edge": 0.0,
        }

    if prob <= 0 or prob >= 1:
        return {
            "valid": False,
            "reason": "Probabilità non valida",
            "stake": 0.0,
            "kelly_pct": 0.0,
            "edge": 0.0,
        }

    if bet_type == "LAY":
        # ── FORMULA KELLY PER LAY ─────────────────────────────────────────
        # Probabilità che il LAY vinca = evento DC NON si verifica
        p_win = 1.0 - prob      # prob che il lay vinca (esito scoperto non esce)
        p_loss = prob           # prob che il lay perda (esito DC si verifica)

        # Guadagno netto per €1 di stake in caso di LAY vincente
        # (meno commissione Betfair sul profitto)
        b_net = 1.0 * (1.0 - commission)   # guadagno netto = stake * (1 - comm)

        # Liability per €1 di stake in caso di LAY perdente
        liability_per_unit = odds - 1.0    # perdiamo (odds-1) per ogni € di stake

        # Kelly LAY: massimizza log(bankroll) considerando guadagno/liability
        # f* = (p_win * b_net - p_loss * liability_per_unit) / (b_net * liability_per_unit)
        numerator = p_win * b_net - p_loss * liability_per_unit
        denominator = b_net * liability_per_unit

        if denominator <= 0:
            return {
                "valid": False,
                "reason": "Denominatore Kelly non valido",
                "stake": 0.0,
                "kelly_pct": 0.0,
                "edge": 0.0,
            }

        kelly_full = numerator / denominator

        # Edge netto: quanto guadagniamo in % rispetto al rischio
        # = (p_win * b_net - p_loss * liability_per_unit)
        edge_per_unit = p_win * b_net - p_loss * liability_per_unit
        edge_pct = (edge_per_unit / liability_per_unit) * 100

    else:
        # ── FORMULA KELLY PER BACK (invariata, con commissione) ──────────
        b_net = (odds - 1.0) * (1.0 - commission)
        q = 1.0 - prob
        kelly_full = (b_net * prob - q) / b_net
        implied_prob = 1.0 / odds
        edge_pct = ((prob - implied_prob) / implied_prob) * 100

    if kelly_full <= 0:
        return {
            "valid": False,
            "reason": f"Kelly negativo ({kelly_full:.4f}) — nessun edge reale",
            "stake": 0.0,
            "kelly_pct": 0.0,
            "edge": round(edge_pct, 2) if bet_type == "LAY" else 0.0,
        }

    # Applica frazione Kelly (conservativa: 0.25 per LAY su mercati illiquidi)
    kelly_used = kelly_full * kelly_frac
    stake_raw = bankroll * kelly_used

    # Clamp min/max
    stake = max(min_stake, min(max_stake, stake_raw))
    stake = round(stake, 2)

    # Liability massima che stiamo rischiando
    max_liability = stake * (odds - 1.0)

    logger.debug(
        f"Kelly [{bet_type}]: prob={prob:.3f} odds={odds:.2f} "
        f"commission={commission:.0%} edge={edge_pct:.1f}% "
        f"kelly_full={kelly_full:.4f} kelly_used={kelly_used:.4f} "
        f"stake_raw={stake_raw:.2f} stake={stake:.2f} "
        f"max_liability={max_liability:.2f}"
    )

    return {
        "valid": True,
        "stake": stake,
        "kelly_pct": round(kelly_used * 100, 2),
        "kelly_full_pct": round(kelly_full * 100, 2),
        "edge": round(edge_pct, 2),          # edge NETTO dopo commissione
        "max_liability": round(max_liability, 2),
        "commission_pct": round(commission * 100, 1),
    }


def dc_to_runner_bets(dc_type: str, runners: dict) -> dict:
    """
    Converte il tipo DC nel runner da mettere in LAY.

    DC 1X (home o draw vince) → lay away
    DC X2 (draw o away vince) → lay home
    DC 12 (home o away vince) → lay draw

    Args:
        dc_type: "1X", "X2", "12"
        runners: {"home": selection_id, "draw": selection_id, "away": selection_id}

    Returns:
        {"lay": selection_id, "back_dc": [nomi esiti coperti]}
    """
    if not runners:
        logger.error("runners dict vuoto in dc_to_runner_bets")
        return {}

    if dc_type == "1X":
        lay_id = runners.get("away")
        covered = ["home", "draw"]
    elif dc_type == "X2":
        lay_id = runners.get("home")
        covered = ["draw", "away"]
    elif dc_type == "12":
        lay_id = runners.get("draw")
        covered = ["home", "away"]
    else:
        logger.error(f"dc_type sconosciuto: {dc_type}")
        return {}

    if lay_id is None:
        logger.error(f"selection_id mancante per LAY in DC {dc_type}: runners={runners}")
        return {}

    return {"lay": lay_id, "back_dc": covered}
