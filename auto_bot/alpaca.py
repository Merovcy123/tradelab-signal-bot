# -*- coding: utf-8 -*-
"""Alpaca-Adapter (Krypto, BTC/USD) mit derselben Schnittstelle wie okx.Okx.

Alpaca kennt bei Krypto nur market, limit und stop_limit (kein OCO). Darum:
- Stop liegt als stop_limit-Order BEIM BROKER (Schutz auch zwischen den Prüfungen).
- Ziel prüft der Bot bei jedem Lauf mit den 5-Min-Hochs seit der letzten Prüfung;
  erreicht -> Stop löschen, Market verkaufen.
Gebühren (Live: 0,15 % Maker / 0,25 % Taker) rechnet der Adapter immer selbst ein,
damit das Journal auch im Papierhandel ehrliche Zahlen zeigt.
"""
import calendar, json, time, urllib.request, urllib.error, urllib.parse

SYMBOL = "BTC/USD"
POS = "BTCUSD"
MAKER, TAKER = 0.0015, 0.0025
STOP_PUFFER = 0.01  # Limit des Stops 1 % unter dem Auslösepreis


class AlpacaFehler(Exception):
    pass


class Alpaca:
    def __init__(self, key, secret, basis="https://paper-api.alpaca.markets", daten=None, zustand=None,
                 symbol=SYMBOL, maker=MAKER, taker=TAKER):
        self.key, self.secret, self.basis = key, secret, basis.rstrip("/").removesuffix("/v2")
        self.symbol, self.pos = symbol, symbol.replace("/", "")
        self.maker, self.taker = maker, taker  # Gebühren fürs Journal (z. B. OKX-Sätze)
        self.daten = daten
        self.z = zustand if zustand is not None else {}  # wird vom Bot gesetzt (für Ziel-Prüfung)

    # ------------------------------------------------------------ Grundlagen
    def _req(self, methode, pfad, body=None, versuche=3, basis=None, auth=True):
        url = (basis or self.basis) + pfad
        h = {"Content-Type": "application/json", "User-Agent": "TradeLab-Bot"}
        if auth:
            h.update({"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret})
        daten = json.dumps(body).encode() if body is not None else None
        letzter = None
        for v in range(versuche):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, data=daten, headers=h, method=methode), timeout=20) as r:
                    t = r.read().decode()
                    return json.loads(t) if t else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode()[:300]
                if e.code == 404:
                    return None
                if e.code in (429, 500, 502, 503) and v < versuche - 1:
                    time.sleep(2 * (v + 1)); continue
                raise AlpacaFehler(f"{methode} {pfad}: {e.code} {text}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                letzter = e; time.sleep(2 * (v + 1))
        raise AlpacaFehler(f"Netzwerk: {letzter}")

    # ------------------------------------------------------------ Markt / Konto
    def instrument(self):
        a = self._req("GET", "/v2/assets/" + urllib.parse.quote(self.symbol, safe=""))
        return {"minSz": str(a.get("min_order_size") or "0.0001"), "lotSz": str(a.get("min_trade_increment") or "0.000000001"),
                "tickSz": str(a.get("price_increment") or "1")}

    def preis(self):
        d = self._req("GET", "/v1beta3/crypto/us/latest/trades?symbols=" + urllib.parse.quote(self.symbol, safe=""),
                      basis="https://data.alpaca.markets", auth=False)  # Krypto-Kurse sind öffentlich
        return float(d["trades"][self.symbol]["p"])

    def guthaben(self):
        """-> (usd_verfuegbar, btc_verfuegbar, gesamt_usd)"""
        acc = self._req("GET", "/v2/account")
        pos = self._req("GET", "/v2/positions/" + self.pos)
        btc_frei = float(pos.get("qty_available") or 0) if pos else 0.0
        return float(acc.get("cash") or 0), btc_frei, float(acc.get("equity") or 0)

    # ------------------------------------------------------------ Normalisierung (OKX-Format für den Bot)
    def _norm(self, o, seite):
        if not o:
            return None
        status = {"filled": "filled", "partially_filled": "partially_filled", "canceled": "canceled",
                  "expired": "canceled", "rejected": "canceled", "done_for_day": "canceled"}.get(o["status"], "live")
        menge = float(o.get("filled_qty") or 0)
        px = float(o.get("filled_avg_price") or 0)
        if seite == "buy":
            rate = self.taker if o.get("type") == "market" else self.maker
            fee, ccy = -menge * rate, "BTC"
        else:
            fee, ccy = -menge * px * self.taker, "USDT"
        ts = o.get("filled_at") or o.get("updated_at")
        ms = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) * 1000 if ts else int(time.time() * 1000)
        return {"state": status, "accFillSz": str(menge), "avgPx": str(px), "fee": str(fee), "feeCcy": ccy,
                "fillTime": str(ms), "ordId": o["id"], "clOrdId": o.get("client_order_id")}

    def _roh(self, cl_id=None, ord_id=None):
        if cl_id:
            return self._req("GET", "/v2/orders:by_client_order_id?client_order_id=" + urllib.parse.quote(cl_id))
        return self._req("GET", f"/v2/orders/{ord_id}")

    # ------------------------------------------------------------ Orders
    def kaufen_limit(self, cl_id, preis, menge, post_only=True):
        return self._req("POST", "/v2/orders", {"symbol": self.symbol, "qty": str(menge), "side": "buy", "type": "limit",
                                                "time_in_force": "gtc", "limit_price": str(preis), "client_order_id": cl_id})

    def verkaufen_market(self, cl_id, menge):
        return self._req("POST", "/v2/orders", {"symbol": self.symbol, "qty": str(menge), "side": "sell", "type": "market",
                                                "time_in_force": "gtc", "client_order_id": cl_id})

    def order(self, cl_id=None, ord_id=None):
        o = self._roh(cl_id, ord_id)
        return self._norm(o, o["side"]) if o else None

    def order_loeschen(self, cl_id):
        o = self._roh(cl_id=cl_id)
        if o and o["status"] in ("new", "accepted", "partially_filled", "pending_new"):
            try:
                self._req("DELETE", f"/v2/orders/{o['id']}")
            except AlpacaFehler as e:
                if "422" not in str(e):
                    raise
            for _ in range(5):  # warten bis wirklich gelöscht
                o = self._roh(cl_id=cl_id)
                if o["status"] not in ("pending_cancel", "new", "accepted"):
                    break
                time.sleep(1)

    # ------------------------------------------------------------ "OCO": Stop beim Broker, Ziel per Bot
    def oco_setzen(self, algo_cl_id, menge, stop, ziel):
        _, btc, _ = self.guthaben()
        menge = min(float(menge), btc)
        tick = 1.0
        limit = round(stop * (1 - STOP_PUFFER) / tick) * tick
        o = self._req("POST", "/v2/orders", {"symbol": self.symbol, "qty": f"{menge:.9f}".rstrip("0").rstrip("."), "side": "sell",
                                             "type": "stop_limit", "time_in_force": "gtc", "stop_price": str(stop),
                                             "limit_price": str(limit), "client_order_id": algo_cl_id})
        self.z.setdefault("ziele", {})[algo_cl_id] = {"ziel": ziel, "menge": menge, "seit": int(time.time()), "geprueft": int(time.time())}
        return {"algoId": o["id"]}

    def oco(self, algo_cl_id):
        """Liefert {state, actualSide, ordId, algoId} wie OKX. Prüft dabei das Ziel."""
        o = self._roh(cl_id=algo_cl_id)
        if not o:
            return None
        info = self.z.setdefault("ziele", {}).get(algo_cl_id, {})
        if o["status"] == "filled":
            return {"state": "effective", "actualSide": "sl", "ordId": o["id"], "algoId": o["id"]}
        if info.get("tp_order"):  # Ziel wurde schon ausgelöst
            return {"state": "effective", "actualSide": "tp", "ordId": info["tp_order"], "algoId": o["id"]}
        if info.get("sl_order"):  # Notverkauf wurde schon ausgelöst
            return {"state": "effective", "actualSide": "sl", "ordId": info["sl_order"], "algoId": o["id"]}
        if o["status"] in ("canceled", "expired", "rejected"):
            return {"state": "canceled", "algoId": o["id"]}
        # Schutz bei Kurssturz: Stop ausgelöst, aber Limit nicht gefüllt -> sofort Market verkaufen
        if o["status"] in ("new", "accepted", "partially_filled") and o.get("limit_price"):
            if self.preis() < float(o["limit_price"]):
                return self._notverkauf(o, algo_cl_id, info)
        # Ziel erreicht? 5-Min-Hochs seit letzter Prüfung (wie im Backtest)
        if info and self.daten:
            hoch = max([x["h"] for x in self.daten.kerzen_5m(info["geprueft"] - 300)] + [0])
            info["geprueft"] = int(time.time())
            if hoch >= info["ziel"]:
                self._req("DELETE", f"/v2/orders/{o['id']}")
                for _ in range(5):
                    o2 = self._roh(cl_id=algo_cl_id)
                    if o2["status"] == "filled":
                        return {"state": "effective", "actualSide": "sl", "ordId": o2["id"], "algoId": o2["id"]}
                    if o2["status"] == "canceled":
                        break
                    time.sleep(1)
                _, btc, _ = self.guthaben()
                v = self.verkaufen_market(algo_cl_id + "tp", min(info["menge"], btc))
                info["tp_order"] = v["id"]
                time.sleep(2)
                return {"state": "effective", "actualSide": "tp", "ordId": v["id"], "algoId": o["id"]}
        return {"state": "live", "algoId": o["id"]}

    def _notverkauf(self, o, algo_cl_id, info):
        try:
            self._req("DELETE", f"/v2/orders/{o['id']}")
        except AlpacaFehler as e:
            if "422" not in str(e):
                raise
        for _ in range(5):
            o2 = self._roh(cl_id=algo_cl_id)
            if o2["status"] == "filled":
                return {"state": "effective", "actualSide": "sl", "ordId": o2["id"], "algoId": o2["id"]}
            if o2["status"] == "canceled":
                break
            time.sleep(1)
        _, btc, _ = self.guthaben()
        v = self.verkaufen_market(algo_cl_id + "sl", min(info.get("menge", btc), btc))
        info["sl_order"] = v["id"]
        time.sleep(2)
        return {"state": "effective", "actualSide": "sl", "ordId": v["id"], "algoId": o["id"]}

    def oco_loeschen(self, algo_id):
        try:
            self._req("DELETE", f"/v2/orders/{algo_id}")
        except AlpacaFehler as e:
            if "422" not in str(e):
                raise
        time.sleep(1)
