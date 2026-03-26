import sys
import os
import logging
from datetime import datetime

# Setup path per importare i tuoi moduli
sys.path.append(os.path.expanduser('~/betfair_bot'))

try:
    import betfairlightweight
    from auth import get_session
    from config import BETFAIR_CONFIG
    from market_finder import find_market
except ImportError as e:
    print(f"Errore Import: {e}")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)

def run():
    # Inizializza client
    trading = betfairlightweight.APIClient(
        username=BETFAIR_CONFIG['username'],
        password=BETFAIR_CONFIG['password'],
        app_key=BETFAIR_CONFIG['app_key']
    )

    # Login con il nostro metodo funzionante
    token = get_session()
    if not token:
        print("❌ Login fallito")
        return
    
    trading.session_token = token
    print("✅ Sessione Betfair.it attiva")

    # TEST MAPPING: Cerchiamo l'Italia di stasera
    # Forziamo il bypass del competition_id 81 perché le nazionali non lo usano
    print("\nRicerca match: Italy vs Northern Ireland...")
    
    # Usiamo direttamente list_market_catalogue per testare il fuzzy match
    from betfairlightweight.filters import market_filter
    filtro = market_filter(
        event_type_ids=["1"],
        text_query="Italy",
        market_type_codes=["MATCH_ODDS"]
    )
    
    try:
        catalogo = trading.betting.list_market_catalogue(
            filter=filtro,
            market_projection=["EVENT", "RUNNER_DESCRIPTION"],
            max_results=5
        )
        
        if catalogo:
            m = catalogo[0]
            print(f"🎯 Match trovato: {m.event.name} (ID: {m.market_id})")
            print("--- Runners ---")
            for r in m.runners:
                print(f"ID: {r.selection_id} | Nome: {r.runner_name}")
        else:
            print("❌ Nessun mercato trovato con query 'Italy'")
            
    except Exception as e:
        print(f"❌ Errore API: {e}")

if __name__ == "__main__":
    run()
