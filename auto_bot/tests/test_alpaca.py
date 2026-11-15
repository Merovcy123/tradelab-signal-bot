# -*- coding: utf-8 -*-
"""Offline-Test des Alpaca-Adapters mit gefälschter API (keine Schlüssel nötig)."""
import os, sys, time, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alpaca import Alpaca


class FakeDaten:
    def __init__(self): self.hoch = 100.0
    def kerzen_5m(self, seit): return [{"t": int(time.time()) - 300, "h": self.hoch, "fertig": True}]


class FakeAlpaca(Alpaca):
    def __init__(self, daten):
        super().__init__("k", "s", daten=daten)
        self.orders, self.btc, self.cash = {}, 0.0, 10000.0

    def _req(self, m, pfad, body=None, versuche=3, basis=None, auth=True):
        if pfad == "/v2/account": return {"cash": self.cash, "equity": self.cash + self.btc * 100}
        if pfad.startswith("/v2/positions"):
            gesperrt = sum(float(o["qty"]) for o in self.orders.values() if o["side"] == "sell" and o["status"] == "new")
            return {"qty_available": self.btc - gesperrt} if self.btc else None
        if pfad.startswith("/v2/assets"): return {"min_order_size": "0.0001", "min_trade_increment": "0.000000001", "price_increment": "1"}
        if m == "POST":
            o = dict(body, id=str(uuid.uuid4()), status="new", filled_qty="0", filled_avg_price=None, updated_at="2026-11-15T10:00:00Z")
            if body["type"] == "market":
                o.update(status="filled", filled_qty=body["qty"], filled_avg_price="110", filled_at="2026-11-15T10:05:00Z")
                self.btc -= float(body["qty"])
            self.orders[o["id"]] = o; return o
        if m == "DELETE":
            self.orders[pfad.split("/")[-1]]["status"] = "canceled"; return {}
        if "by_client_order_id" in pfad:
            cl = pfad.split("=")[-1]
            return next((o for o in self.orders.values() if o["client_order_id"] == cl), None)
        return self.orders.get(pfad.split("/")[-1])


def test_ablauf_ziel():
    d = FakeDaten(); a = FakeAlpaca(d); a.z = {}
    a.kaufen_limit("v3kauf", 95, 0.01)
    o = a.order(cl_id="v3kauf"); assert o["state"] == "live"
    roh = a._roh(cl_id="v3kauf"); roh.update(status="filled", filled_qty="0.01", filled_avg_price="95", filled_at="2026-11-15T09:00:00Z")
    a.btc = 0.01
    o = a.order(cl_id="v3kauf"); assert o["state"] == "filled" and float(o["fee"]) < 0 and o["feeCcy"] == "BTC"
    a.oco_setzen("v3oco", 0.01, 90, 105)
    assert a.oco("v3oco")["state"] == "live"          # Hoch 100 < Ziel 105
    d.hoch = 106
    r = a.oco("v3oco"); assert r["state"] == "effective" and r["actualSide"] == "tp"
    v = a.order(ord_id=r["ordId"]); assert v["state"] == "filled" and v["feeCcy"] == "USDT"
    assert a.oco("v3oco")["actualSide"] == "tp"       # bleibt stabil (idempotent)
    assert abs(a.btc) < 1e-12
    print("OK test_ablauf_ziel")


def test_ablauf_stop():
    d = FakeDaten(); a = FakeAlpaca(d); a.z = {}; a.btc = 0.01
    a.oco_setzen("v3oco2", 0.01, 90, 105)
    a._roh(cl_id="v3oco2").update(status="filled", filled_qty="0.01", filled_avg_price="89.5", filled_at="2026-11-15T11:00:00Z")
    r = a.oco("v3oco2"); assert r["state"] == "effective" and r["actualSide"] == "sl"
    print("OK test_ablauf_stop")


def test_loeschen():
    a = FakeAlpaca(FakeDaten()); a.kaufen_limit("x1", 50, 0.001)
    a.order_loeschen("x1"); assert a.order(cl_id="x1")["state"] == "canceled"
    print("OK test_loeschen")


if __name__ == "__main__":
    test_ablauf_ziel(); test_ablauf_stop(); test_loeschen()
