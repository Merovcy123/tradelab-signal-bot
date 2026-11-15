# -*- coding: utf-8 -*-
"""Replay: 5 Jahre BTCUSDT-5-Min-Kerzen laufen durch die echte Bot-Logik mit simuliertem OKX-Broker.
Aufruf: python3 replay.py <pfad_zu_btc5m.csv oder .pkl>"""
import os, sys, tempfile, time
import numpy as np, pandas as pd
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER)); sys.path.insert(0, HIER)
from bot import Bot  # noqa
from sim import SimDaten, SimOkx  # noqa

pfad = sys.argv[1]
d = pd.read_pickle(pfad) if pfad.endswith(".pkl") else pd.read_csv(pfad, index_col=0, parse_dates=True)
t = ((d.index - pd.Timestamp(0)) // pd.Timedelta(seconds=1)).values.astype(np.int64)
daten = SimDaten(t, d.o.values, d.h.values, d.l.values, d.c.values)
broker = SimOkx(daten, 5000.0)
nachrichten = []
ordner = tempfile.mkdtemp()
bot = Bot(broker, daten, lambda a, b="": nachrichten.append(a), ordner, {}, uhr=lambda: daten.jetzt)
bot.log = lambda text: None
start = np.searchsorted(t, t[0] + 25 * 86400)
t0 = time.time()
for i in range(start, len(t)):
    daten.jetzt = int(t[i])
    if i % 2 == 0 or True:
        bot.tick()
    broker.kerze(i)
j = pd.read_csv(os.path.join(ordner, "journal.csv"), sep=";", parse_dates=["eroeffnet_utc"])
print(f"Laufzeit {time.time() - t0:.0f}s · Trades {len(j)} · Gründe {j.grund.value_counts().to_dict()}")
for name, s in [("bis Juni 2024", j[j.eroeffnet_utc < "2024-07-01"]), ("ab Juli 2024", j[j.eroeffnet_utc >= "2024-07-01"])]:
    r = s.r
    print(f"{name}: n {len(r)} · Treffer {(r > 0).mean() * 100:.1f} % · PF {r[r > 0].sum() / -r[r <= 0].sum():.2f} · Ø {r.mean():+.3f} R")
print(f"Konto Ende: {j.konto_usdt.iloc[-1]:.2f} USDT (Start 5000) · Tagesplan-Nachrichten {sum('Tagesplan' in n for n in nachrichten)}"
      f" · Alarme {sum('🚨' in n for n in nachrichten)}")
j.to_csv(os.path.join(HIER, "replay_journal.csv"), sep=";", index=False)
