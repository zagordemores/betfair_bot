"""
logger.py — log CSV + notifiche Telegram
"""

import csv
import logging
import os
import requests
from datetime import datetime
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, BETS_LOG_CSV

logger = logging.getLogger(__name__)

CSV_HEADERS = [
    "timestamp", "dry_run", "home", "away", "league", "date",
    "dc_type", "odds", "prob_model", "edge_pct", "kelly_pct",
    "stake", "market_id", "bet_id", "status", "result", "profit"
]


def init_csv():
    """Crea il file CSV se non esiste."""
    if not os.path.exists(BETS_LOG_CSV):
        os.makedirs(os.path.dirname(BETS_LOG_CSV), exist_ok=True)
        with open(BETS_LOG_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
            writer.writeheader()
        logger.info(f"CSV creato: {BETS_LOG_CSV}")


def log_bet(bet: dict):
    """Salva una scommessa nel CSV."""
    init_csv()
    row = {h: bet.get(h, "") for h in CSV_HEADERS}
    row["timestamp"] = datetime.now().isoformat()
    with open(BETS_LOG_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writerow(row)
    logger.info(f"Bet loggata: {row['home']} vs {row['away']} {row['dc_type']} @{row['odds']}")


def send_telegram(message: str):
    """Invia notifica Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug("Telegram non configurato, skip notifica")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=5)
        if not resp.ok:
            logger.warning(f"Telegram error: {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram non raggiungibile: {e}")


def notify_bet_placed(bet: dict, dry_run: bool):
    """Notifica Telegram quando una scommessa viene piazzata."""
    mode = "🔵 DRY RUN" if dry_run else "🟢 LIVE"
    msg = (
        f"{mode} — Scommessa piazzata\n\n"
        f"⚽ <b>{bet['home']} vs {bet['away']}</b>\n"
        f"📅 {bet.get('date', 'N/D')}\n"
        f"🎯 <b>{bet['dc_type']}</b> @{bet['odds']}\n"
        f"📊 Edge: +{bet['edge_pct']}% | Kelly: {bet['kelly_pct']}%\n"
        f"💶 Stake: €{bet['stake']}\n"
        f"🏆 {bet.get('league', '').replace('_', ' ').title()}"
    )
    send_telegram(msg)


def notify_error(error: str):
    """Notifica Telegram in caso di errore critico."""
    send_telegram(f"🔴 <b>ERRORE BOT</b>\n{error}")


def notify_startup(dry_run: bool, n_bets: int):
    """Notifica avvio bot."""
    mode = "DRY RUN" if dry_run else "LIVE"
    send_telegram(
        f"🚀 <b>Bot avviato</b> [{mode}]\n"
        f"Value bets caricate: {n_bets}\n"
        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )


def daily_summary():
    """Legge il CSV e invia summary giornaliero."""
    init_csv()
    today = datetime.now().strftime("%Y-%m-%d")
    bets_today = []
    try:
        with open(BETS_LOG_CSV, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["timestamp"].startswith(today) and row["status"] == "matched":
                    bets_today.append(row)
    except Exception as e:
        logger.error(f"Errore lettura CSV: {e}")
        return

    if not bets_today:
        send_telegram(f"📊 Summary {today}: nessuna scommessa matchata oggi")
        return

    total_stake  = sum(float(b["stake"])  for b in bets_today)
    total_profit = sum(float(b["profit"]) for b in bets_today if b["profit"])
    roi = (total_profit / total_stake * 100) if total_stake > 0 else 0

    lines = [f"📊 <b>Summary {today}</b>", f"Scommesse: {len(bets_today)}",
             f"Staked: €{total_stake:.2f}", f"P&amp;L: €{total_profit:.2f}",
             f"ROI: {roi:.1f}%", "", "Dettaglio:"]
    for b in bets_today:
        p = float(b["profit"]) if b["profit"] else 0
        icon = "✅" if p > 0 else "❌"
        lines.append(f"{icon} {b['home']} vs {b['away']} {b['dc_type']} @{b['odds']} → €{p:.2f}")

    send_telegram("\n".join(lines))
