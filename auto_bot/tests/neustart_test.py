# -*- coding: utf-8 -*-
"""Neustart-Test: 1 Jahr Replay, der Bot wird alle 97 Durchläufe neu gestartet (Zustand nur aus Datei + Broker).
Ergebnis muss gleich sein wie ohne Neustart, ohne doppelte Orders."""
import os, sys, tempfile
import numpy as np, pandas as pd
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER)); sys.path.insert(0, HIER)
from bot import Bot  # noqa
from sim import SimDaten, SimOkx  # noqa

d = pd.read_pickle(sys.argv[1])
d = d[(d.index >= "2024-12-01") & (d.index < "2025-12-01")]
t = ((d.index - pd.Timestamp(0)) // pd.Timedelta(seconds=1)).values.astype(np.int64)


def lauf(neustart):
    daten = SimDaten(t, d.o.values, d.h.values, d.l.values, d.c.values)
    broker = SimOkx(daten, 5000.0)
    ordner = tempfile.mkdtemp()
    mk = lambda: Bot(broker, daten, lambda a, b="": None, ordner, {}, uhr=lambda: daten.jetzt)
    bot = mk(); bot.log = lambda x: None
    for k, i in enumerate(range(np.searchsorted(t, t[0] + 25 * 86400), len(t))):
        daten.jetzt = int(t[i])
        if neustart and k % 97 == 0:
            bot = mk(); bot.log = lambda x: None
        bot.tick()
        broker.kerze(i)
    return pd.read_csv(os.path.join(ordner, "journal.csv"), sep=";")


a, b = lauf(False), lauf(True)
gleich = a[["eroeffnet_utc", "grund", "r"]].round(6).equals(b[["eroeffnet_utc", "grund", "r"]].round(6))
print(f"ohne Neustart {len(a)} Trades, mit Neustart {len(b)} Trades, identisch: {gleich}")
assert gleich
