import betfairlightweight
from betfairlightweight.filters import market_filter
from auth import get_session
from config import BETFAIR_CONFIG

trading = betfairlightweight.APIClient(
    username=BETFAIR_CONFIG['username'],
    password=BETFAIR_CONFIG['password'],
    app_key=BETFAIR_CONFIG['app_key']
)
trading.session_token = get_session()

comps = trading.betting.list_competitions(
    filter=market_filter(event_type_ids=["1"])
)

keywords = ["serie", "premier", "liga", "champions", "europa", "conference", "ligue", "bundesliga"]
print(f"Totale competitions trovate: {len(comps)}")
print("\nCompetitions rilevanti:")
for c in sorted(comps, key=lambda x: x.competition.name):
    name = c.competition.name.lower()
    if any(k in name for k in keywords):
        print(f"  ID={c.competition.id:>10} | {c.competition.name:<45} | {c.market_count} mercati")

print("\nTutte le competitions (per debug):")
for c in sorted(comps, key=lambda x: x.competition.name):
    print(f"  ID={c.competition.id:>10} | {c.competition.name:<45} | {c.market_count} mercati")
