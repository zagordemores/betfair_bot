"""
kelly_sizer.py — calcola stake ottimale da Kelly Criterion
"""

import logging
from config import BANKROLL, MAX_KELLY_FRAC, MIN_STAKE, MAX_STAKE, MIN_ODDS, MAX_ODDS

logger = logging.getLogger(__name__)


def kelly_stake(
    prob: float,
    odds: float,
    bankroll: float = BANKROLL,
    kelly_frac: float = MAX_KELLY_FRAC,
    min_stake: float = MIN_STAKE,
    max_stake: float = MAX_STAKE,
) -> dict:
    """
    Calcola la puntata ottimale secondo il Criterio di Kelly.

    Args:
        prob:       probabilità stimata dal modello (0-1)
        odds:       quota decimale del bookmaker
        bankroll:   capitale totale disponibile
        kelly_frac: frazione di Kelly da usare (0.5 = mezzo Kelly)
        min_stake:  puntata minima in euro
        max_stake:  puntata massima in euro

    Returns:
        dict con stake, kelly_pct, edge, valid
    """
    if odds < MIN_ODDS or odds > MAX_ODDS:
        return {
            "valid":     False,
            "reason":    f"Quota {odds:.2f} fuori range [{MIN_ODDS}-{MAX_ODDS}]",
            "stake":     0.0,
            "kelly_pct": 0.0,
            "edge":      0.0,
        }

    # Probabilità implicita nella quota (con margin ~5%)
    implied_prob = 1.0 / odds

    # Edge percentuale
    edge = (prob - implied_prob) / implied_prob

    if prob <= 0 or prob >= 1:
        return {"valid": False, "reason": "Probabilità non valida", "stake": 0.0, "kelly_pct": 0.0, "edge": edge}

    # Formula Kelly: f = (b*p - q) / b
    # dove b = odds-1, p = prob, q = 1-prob
    b = odds - 1.0
    q = 1.0 - prob
    kelly_full = (b * prob - q) / b

    if kelly_full <= 0:
        return {
            "valid":     False,
            "reason":    f"Kelly negativo ({kelly_full:.3f}) — nessun valore",
            "stake":     0.0,
            "kelly_pct": 0.0,
            "edge":      edge,
        }

    # Applica frazione Kelly
    kelly_used = kelly_full * kelly_frac
    stake_raw  = bankroll * kelly_used

    # Clamp min/max
    stake = max(min_stake, min(max_stake, stake_raw))
    stake = round(stake, 2)

    logger.debug(
        f"Kelly: prob={prob:.3f} odds={odds:.2f} implied={implied_prob:.3f} "
        f"edge={edge:.1%} kelly_full={kelly_full:.3f} kelly_used={kelly_used:.3f} "
        f"stake_raw={stake_raw:.2f} stake={stake:.2f}"
    )

    return {
        "valid":     True,
        "stake":     stake,
        "kelly_pct": round(kelly_used * 100, 2),
        "kelly_full_pct": round(kelly_full * 100, 2),
        "edge":      round(edge * 100, 2),
        "implied_prob": round(implied_prob * 100, 2),
    }


def dc_to_runner_bets(dc_type: str, runners: dict) -> list[str]:
    """
    Converte il tipo DC in lista di selection_id da scommettere.

    Betfair MATCH_ODDS ha 3 runner: home, draw, away.
    Per la DC dobbiamo fare LAY sull'esito non coperto.

    Args:
        dc_type: "1X", "X2", "12"
        runners: {"home": id, "draw": id, "away": id}

    Returns:
        lista di selection_id da BACK
        (su Betfair Exchange si può fare back DC diretta
         oppure lay del singolo esito scoperto)
    """
    if dc_type == "1X":
        # Copre home e draw → lay away
        return {"lay": runners.get("away"), "back_dc": ["home", "draw"]}
    elif dc_type == "X2":
        # Copre draw e away → lay home
        return {"lay": runners.get("home"), "back_dc": ["draw", "away"]}
    elif dc_type == "12":
        # Copre home e away → lay draw
        return {"lay": runners.get("draw"), "back_dc": ["home", "away"]}
    else:
        return {}
