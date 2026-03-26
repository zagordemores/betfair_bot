import logging
from datetime import datetime
import betfairlightweight
from market_finder import find_market
from auth import get_session
from config import BETFAIR_CONFIG

logging.basicConfig(level=logging.INFO)

def run_test():
    # Inizializziamo il client
    trading = betfairlightweight.APIClient(
        username=BETFAIR_CONFIG['username'],
        password=BETFAIR_CONFIG['password'],
        app_key=BETFAIR_CONFIG['app_key']
    )

    token = get_session()
    if token:
        trading.session_token = token
        print("✅ Sessione iniettata correttamente.")
        
        # Test con l'Italia
        print("Ricerca match Italia...")
        # Nota: passiamo una competizione fittizia perché per le amichevoli/nazionali l'ID 81 non vale
        result = find_market(
            trading,
            home="Italy",
            away="Northern Ireland",
            league="serie_a", # Userà i parametri di base
            match_date=datetime.now().strftime("%Y-%m-%d"),
            hours_window=48
        )

        if result:
            print("\n🎯 Mercato Trovato!")
            print(f"  ID: {result['market_id']}")
            print(f"  Runners: {result['runners']}")
        else:
            print("\n❌ Match non trovato. (Probabile discrepanza nei filtri di LEAGUE_MAP)")
    else:
        print("❌ Impossibile ottenere il session token.")

if __name__ == "__main__":
    run_test()
