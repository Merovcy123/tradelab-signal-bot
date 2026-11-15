# -*- coding: utf-8 -*-
"""Replay: P4-Bot mit historischen Daten (Pickles) und Uhr in 10-Min-Schritten. Aufruf: p4_replay.py DATENORDNER VON BIS"""
import os, sys, tempfile, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import p4_bot
ordner, von, bis = sys.argv[1], pd.Timestamp(sys.argv[2]), pd.Timestamp(sys.argv[3])
NAMEN = {"BTCUSDT": "btc", "ETHUSDT": "eth", "ADAUSDT": "ada", "SOLUSDT": "sol"}
uhr = [0]

class Fake:
    def __init__(self, sym):
        d = pd.read_pickle(os.path.join(ordner, NAMEN[sym] + "5m_all.pkl"))
        self.ts = ((d.index - pd.Timestamp(0)) // pd.Timedelta(seconds=1)).values.astype(np.int64)
        self.a = d[["o", "h", "l", "c"]].values
        day = d.resample("1D").agg({"o": "first", "h": "max", "l": "min", "c": "last"})
        self.dts = ((day.index - pd.Timestamp(0)) // pd.Timedelta(seconds=1)).values.astype(np.int64)
        self.da = day[["o", "h", "l", "c"]].values
    def tage(self, n):
        j = np.searchsorted(self.dts, uhr[0], side="right")
        out = []
        for i in range(max(0, j - n), j):
            o, h, l, c = self.da[i]
            fertig = self.dts[i] + 86400 <= uhr[0]
            if not fertig:  # laufender Tag: nur bis jetzt
                a = np.searchsorted(self.ts, self.dts[i]); b = np.searchsorted(self.ts, uhr[0] - 300, side="right")
                seg = self.a[a:b]
                if len(seg) == 0: continue
                o, h, l, c = seg[0, 0], seg[:, 1].max(), seg[:, 2].min(), seg[-1, 3]
            out.append(dict(t=int(self.dts[i]), o=float(o), h=float(h), l=float(l), c=float(c), fertig=bool(fertig)))
        return out
    def kerzen_5m(self, seit):
        a = np.searchsorted(self.ts, seit, side="right"); b = np.searchsorted(self.ts, uhr[0] - 300, side="right")
        return [dict(t=int(self.ts[i]), o=float(self.a[i, 0]), h=float(self.a[i, 1]), l=float(self.a[i, 2]), c=float(self.a[i, 3]), fertig=True) for i in range(a, b)]

fakes = {s: Fake(s) for s in NAMEN}
tmp = tempfile.mkdtemp()
meldungen = []
kw = {}
if os.environ.get("DEMO"):
    from p4_demo import Konto, SHORTS
    konto = Konto(os.path.join(tmp, "demo"))
    kw = dict(ausfuehren=SHORTS, konto_fn=konto.wert, buchung=konto.buchen)
bot = p4_bot.P4Bot(lambda s: fakes[s], lambda t, x="": meldungen.append(t + " | " + x.replace("\n", " / ")), tmp, uhr=lambda: uhr[0], **kw)
bot.log = lambda t: None
t0 = int((von - pd.Timestamp(0)).total_seconds()); t1 = int((bis - pd.Timestamp(0)).total_seconds())
for x in range(t0, t1, 600):
    uhr[0] = x + 60
    if os.environ.get("NEUSTART"):
        bot = p4_bot.P4Bot(lambda s: fakes[s], lambda t, x="": meldungen.append(t), tmp, uhr=lambda: uhr[0], **kw); bot.log = lambda t: None
    bot.tick()
j = pd.read_csv(os.path.join(tmp, "journal.csv"), sep=";")
if os.environ.get("DEMO"):
    print(len(meldungen), "Meldungen, Beispiele:"); [print("  ", m) for m in meldungen[:4]]
    print("Demo-Konto:", round(konto.wert(), 2))
print(f"Bot-Replay: {len(j)} Trades · Summe {j.r.sum():+.1f} R · Konto {bot.z['konto']:.2f}")
print(j.groupby("strategie").r.agg(["count", "sum"]).round(2).to_string())
j.to_csv(os.path.join(tmp, "j.csv"), index=False); print("journal:", tmp)
