import betfairlightweight
from betfairlightweight.filters import market_filter
from datetime import datetime, timedelta
from auth import get_session
from config import BETFAIR_CONFIG

trading = betfairlightweight.APIClient(
    username=BETFAIR_CONFIG['username'],
    password=BETFAIR_CONFIG['password'],
    app_key=BETFAIR_CONFIG['app_key']
)
trading.session_token = get_session()

leagues = {
    "Serie A":          "81",
    "Premier League":   "10932509",
    "La Liga":          "117",
    "Champions League": "228",
    "Europa League":    "2005",
    "Conference":       "12375833",
}

from_dt = datetime.utcnow()
to_dt   = datetime.utcnow() + timedelta(days=15)

for name, comp_id in leagues.items():
    cat = trading.betting.list_market_catalogue(
        filter=market_filter(
            event_type_ids=["1"],
            competition_ids=[comp_id],
            market_type_codes=["MATCH_ODDS"],
            market_start_time={
                "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to":   to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        ),
        market_projection=["EVENT", "MARKET_START_TIME"],
        max_results=20,
    )
    print(f"\n{name}: {len(cat)} mercati aperti")
    for m in cat:
        print(f"  {m.market_id} | {m.event.name if m.event else '?'} | {m.market_start_time}")
