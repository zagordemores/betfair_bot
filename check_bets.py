import pandas as pd
import os

path = os.path.expanduser('~/betfair_bot/bets_history.csv')
if not os.path.exists(path):
    print("Nessuna scommessa registrata ancora.")
else:
    df = pd.read_csv(path)
    total_profit = df['profit'].sum()
    print(f"--- RECAP BETBOT ---")
    print(f"Scommesse effettuate: {len(df)}")
    print(f"Profitto/Perdita Totale (Virtuale): €{total_profit:.2f}")
    print(f"--------------------")
    print(df.tail(5)) # Mostra le ultime 5
