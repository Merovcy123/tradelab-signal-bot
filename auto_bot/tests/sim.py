# -*- coding: utf-8 -*-
"""Simulierter OKX-Broker + Kursdaten aus historischen 5-Min-Kerzen (für Replay-Tests)."""
import numpy as np

MAKER, TAKER = 0.0008, 0.0010


class SimDaten:
    def __init__(self, t, o, h, l, c):
        self.t, self.o, self.h, self.l, self.c = t, o, h, l, c
        self.jetzt = 0
        # Tageskerzen vorberechnen
        tag = t // 86400 * 86400
        self.tage_t, start = np.unique(tag, return_index=True)
        ende = np.r_[start[1:], len(t)]
        self.td = [(self.tage_t[k], start[k], ende[k]) for k in range(len(start))]

    def tage(self, anzahl=60):
        out = []
        for tt, a, b in self.td:
            if tt > self.jetzt:
                break
            fertig = tt + 86400 <= self.jetzt
            bb = b if fertig else np.searchsorted(self.t, self.jetzt)  # nur abgeschlossene 5m-Kerzen
            if bb <= a:
                out.append(dict(t=int(tt), o=float(self.o[a]), h=float(self.o[a]), l=float(self.o[a]), c=float(self.o[a]), fertig=False))
                continue
            out.append(dict(t=int(tt), o=float(self.o[a]), h=float(self.h[a:bb].max()), l=float(self.l[a:bb].min()),
                            c=float(self.c[bb - 1]), fertig=fertig))
        return out[-anzahl:]

    def kerzen_5m(self, seit):
        a = np.searchsorted(self.t, seit, side="right")
        b = np.searchsorted(self.t, self.jetzt - 300, side="right")
        return [dict(t=int(self.t[i]), o=float(self.o[i]), h=float(self.h[i]), l=float(self.l[i]), c=float(self.c[i]), fertig=True)
                for i in range(a, b)]


class SimOkx:
    def __init__(self, daten, usdt=5000.0):
        self.d = daten
        self.usdt, self.btc = usdt, 0.0
        self.btc_frei = 0.0
        self.orders, self.algos = {}, {}
        self.n = 0

    # Markt / Konto
    def instrument(self):
        return {"minSz": "0.00001", "lotSz": "0.00000001", "tickSz": "0.1"}

    def preis(self):
        i = np.searchsorted(self.d.t, self.d.jetzt)
        return float(self.d.o[min(i, len(self.d.o) - 1)])

    def guthaben(self):
        return self.usdt_frei(), self.btc_frei, self.usdt + self.btc * self.preis()

    def usdt_frei(self):
        gebunden = sum(o["px"] * o["sz"] for o in self.orders.values() if o["side"] == "buy" and o["state"] == "live")
        return self.usdt - gebunden

    # Orders
    def _id(self):
        self.n += 1
        return str(self.n)

    def kaufen_limit(self, cl, preis, menge, post_only=True):
        if cl in self.orders:
            raise Exception("51016 doppelte clOrdId")
        p = self.preis()
        if post_only and p <= preis:
            o = dict(ordId=self._id(), clOrdId=cl, side="buy", px=preis, sz=menge, state="canceled", accFillSz="0", avgPx="0", fee="0", feeCcy="BTC")
        else:
            o = dict(ordId=self._id(), clOrdId=cl, side="buy", px=preis, sz=menge, state="live", accFillSz="0", avgPx="0", fee="0", feeCcy="BTC",
                     sofort=p <= preis)
        self.orders[cl] = o
        self.orders["#" + o["ordId"]] = o
        if o.get("sofort"):
            self._fill_buy(o, p, TAKER)
        return o

    def _fill_buy(self, o, px, fee_rate):
        o.update(state="filled", accFillSz=str(o["sz"]), avgPx=str(px), fee=str(-o["sz"] * fee_rate), fillTime=str(self.d.jetzt * 1000))
        self.usdt -= o["sz"] * px
        netto = o["sz"] * (1 - fee_rate)
        self.btc += netto
        self.btc_frei += netto

    def verkaufen_market(self, cl, menge):
        p = self.preis()
        menge = min(menge, self.btc_frei)
        o = dict(ordId=self._id(), clOrdId=cl, side="sell", sz=menge, state="filled", accFillSz=str(menge), avgPx=str(p),
                 fee=str(-menge * p * TAKER), feeCcy="USDT", fillTime=str(self.d.jetzt * 1000))
        self.orders[cl] = o
        self.orders["#" + o["ordId"]] = o
        self.btc -= menge
        self.btc_frei -= menge
        self.usdt += menge * p * (1 - TAKER)
        return o

    def order(self, cl_id=None, ord_id=None):
        return self.orders.get(cl_id) if cl_id else self.orders.get("#" + str(ord_id))

    def order_loeschen(self, cl):
        o = self.orders.get(cl)
        if o and o["state"] == "live":
            o["state"] = "canceled"

    def oco_setzen(self, acl, menge, stop, ziel):
        if acl in self.algos:
            raise Exception("51016 doppelte algoClOrdId")
        if menge > self.btc_frei + 1e-12:
            raise Exception("51008 zu wenig BTC")
        a = dict(algoId=self._id(), algoClOrdId=acl, sz=menge, sl=stop, tp=ziel, state="live", seit=self.d.jetzt)
        self.algos[acl] = a
        self.btc_frei -= menge
        return a

    def oco(self, acl):
        return self.algos.get(acl)

    def oco_loeschen(self, algo_id):
        for a in self.algos.values():
            if a["algoId"] == algo_id and a["state"] == "live":
                a["state"] = "canceled"
                self.btc_frei += a["sz"]

    # Kerze durchspielen (Ausführungen während der Kerze)
    def kerze(self, i):
        o_, h, l = float(self.d.o[i]), float(self.d.h[i]), float(self.d.l[i])
        for o in list(self.orders.values()):
            if o["side"] == "buy" and o["state"] == "live" and l <= o["px"]:
                self._fill_buy(o, min(o["px"], o_), MAKER)
        for a in self.algos.values():
            if a["state"] != "live" or a["seit"] > self.d.t[i]:
                continue
            seite = "sl" if l <= a["sl"] else ("tp" if h >= a["tp"] else None)
            if not seite:
                continue
            px = min(a["sl"], o_) if seite == "sl" else max(a["tp"], o_) if False else a["tp"]
            self.btc_frei += a["sz"]
            v = self.verkaufen_market("algo" + a["algoId"], a["sz"])
            # Preis der Auslösung statt Kerzen-Eröffnung
            menge = float(v["accFillSz"])
            self.usdt += menge * (px - float(v["avgPx"])) * (1 - TAKER)
            v.update(avgPx=str(px), fee=str(-menge * px * TAKER))
            a.update(state="effective", actualSide=seite, ordId=v["ordId"])
