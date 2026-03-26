import sys
import os
import logging
import betfairlightweight

sys.path.append(os.path.expanduser('~/betfair_bot'))
from auth import get_session
from config import BETFAIR_CONFIG

logging.basicConfig(level=logging.INFO)

def run():
    trading = betfairlightweight.APIClient(
        username=BETFAIR_CONFIG['username'],
        password=BETFAIR_CONFIG['password'],
        app_key=BETFAIR_CONFIG['app_key']
    )

    token = get_session()
    if not token:
        print("ERRORE: Login fallito")
        return
    
    trading.session_token = token
    print("SESSIONE: Attiva su Betfair.it")

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
            print("MATCH TROVATO: " + str(m.event.name))
            print("MARKET ID: " + str(m.market_id))
            print("--- RUNNERS ---")
            for r in m.runners:
                print("ID: " + str(r.selection_id) + " | Nome: " + str(r.runner_name))
        else:
            print("AVVISO: Nessun mercato trovato")
    except Exception as e:
        print("ERRORE API: " + str(e))

if __name__ == "__main__":
    run()
