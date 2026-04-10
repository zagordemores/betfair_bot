"""
auth.py — Gestione sessione Betfair.it

PATCH v2:
  - Aggiunto refresh automatico del token ogni 3.5 ore
  - Il token viene cachato in memoria e rinnovato prima della scadenza
  - Thread-safe con threading.Lock
"""

import requests
import threading
import time
import logging
from config import BETFAIR_CONFIG

logger = logging.getLogger(__name__)

# ── CACHE TOKEN ──────────────────────────────────────────────────────────────
_token        = None
_token_time   = 0
_token_lock   = threading.Lock()

# Betfair token dura ~4h — refreshiamo a 3.5h per sicurezza
TOKEN_TTL     = 3.5 * 3600   # secondi


def _login() -> str:
    """Esegue il login su Betfair.it e restituisce il token."""
    url = "https://identitysso.betfair.it/api/login"
    payload = {
        'username': BETFAIR_CONFIG['username'],
        'password': BETFAIR_CONFIG['password']
    }
    headers = {
        'X-Application':  BETFAIR_CONFIG['app_key'],
        'Content-Type':   'application/x-www-form-urlencoded',
        'Accept':         'application/json'
    }
    try:
        r = requests.post(url, data=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            token = data.get('token')
            if token:
                logger.info("Login Betfair.it OK — token ottenuto")
                return token
            else:
                logger.error(f"Login fallito: {data.get('error', 'risposta senza token')}")
        else:
            logger.error(f"Login HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Errore connessione login: {e}")
    return None


def get_session() -> str:
    """
    Restituisce un token di sessione valido.
    Se il token è scaduto o assente, esegue un nuovo login automaticamente.
    Thread-safe.
    """
    global _token, _token_time

    with _token_lock:
        now = time.time()
        if _token and (now - _token_time) < TOKEN_TTL:
            # Token ancora valido
            remaining = TOKEN_TTL - (now - _token_time)
            logger.debug(f"Token in cache — scade tra {remaining/60:.0f} min")
            return _token

        # Token scaduto o assente — rinnova
        logger.info("Token scaduto o assente — rinnovo sessione Betfair.it...")
        token = _login()
        if token:
            _token      = token
            _token_time = now
        else:
            logger.critical("Impossibile ottenere il token Betfair.it")
            _token = None

        return _token


def start_keepalive(interval: float = TOKEN_TTL - 300) -> threading.Thread:
    """
    Avvia un thread che rinnova il token in background ogni (TTL - 5min).
    Opzionale — get_session() gestisce già il refresh lazy.
    Utile se vuoi garantire zero downtime durante le partite.

    Args:
        interval: secondi tra un refresh e l'altro (default 3h55m)
    """
    def _loop():
        while True:
            time.sleep(interval)
            logger.info("Keep-alive: rinnovo token Betfair.it...")
            get_session()

    t = threading.Thread(target=_loop, daemon=True, name="betfair-keepalive")
    t.start()
    logger.info(f"Keep-alive avviato — refresh ogni {interval/3600:.1f}h")
    return t
