# -*- coding: utf-8 -*-
"""Kleiner OKX-API-v5-Client (nur Standardbibliothek). Demo-Modus über Header x-simulated-trading: 1."""
import base64, hashlib, hmac, json, time, urllib.request, urllib.error
from datetime import datetime, timezone

BASIS = "https://www.okx.com"
INST = "BTC-USDT"


class OkxFehler(Exception):
    pass


class Okx:
    def __init__(self, key, secret, passphrase, demo=True, basis=BASIS):
        self.key, self.secret, self.passphrase, self.demo, self.basis = key, secret, passphrase, demo, basis

    # ------------------------------------------------------------ Grundlagen
    def _req(self, methode, pfad, body=None, privat=True, versuche=3):
        daten = json.dumps(body) if body is not None else ""
        letzter = None
        for v in range(versuche):
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
            h = {"Content-Type": "application/json", "User-Agent": "TradeLab-Bot"}
            if privat:
                sig = base64.b64encode(hmac.new(self.secret.encode(), (ts + methode + pfad + daten).encode(),
                                                hashlib.sha256).digest()).decode()
                h.update({"OK-ACCESS-KEY": self.key, "OK-ACCESS-SIGN": sig, "OK-ACCESS-TIMESTAMP": ts,
                          "OK-ACCESS-PASSPHRASE": self.passphrase})
            if self.demo:
                h["x-simulated-trading"] = "1"
            req = urllib.request.Request(self.basis + pfad, data=daten.encode() if daten else None, headers=h, method=methode)
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    j = json.loads(r.read().decode())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                letzter = e
                time.sleep(2 * (v + 1))
                continue
            if j.get("code") not in ("0", 0):
                raise OkxFehler(f"{pfad}: {j.get('code')} {j.get('msg')} {json.dumps(j.get('data'))[:300]}")
            return j.get("data", [])
        raise OkxFehler(f"Netzwerk: {letzter}")

    # ------------------------------------------------------------ Markt
    def instrument(self):
        d = self._req("GET", f"/api/v5/public/instruments?instType=SPOT&instId={INST}", privat=False)[0]
        return {"minSz": d["minSz"], "lotSz": d["lotSz"], "tickSz": d["tickSz"]}

    def preis(self):
        return float(self._req("GET", f"/api/v5/market/ticker?instId={INST}", privat=False)[0]["last"])

    # ------------------------------------------------------------ Konto
    def guthaben(self):
        """-> (usdt_verfuegbar, btc_verfuegbar, gesamt_usd)"""
        d = self._req("GET", "/api/v5/account/balance?ccy=BTC,USDT")[0]
        werte = {x["ccy"]: x for x in d.get("details", [])}
        usdt = float(werte.get("USDT", {}).get("availBal") or 0)
        btc = float(werte.get("BTC", {}).get("availBal") or 0)
        btc_ges = float(werte.get("BTC", {}).get("cashBal") or 0)
        gesamt = float(werte.get("USDT", {}).get("cashBal") or 0) + btc_ges * self.preis()
        return usdt, btc, gesamt

    # ------------------------------------------------------------ Orders
    def kaufen_limit(self, cl_id, preis, menge, post_only=True):
        return self._req("POST", "/api/v5/trade/order", {
            "instId": INST, "tdMode": "cash", "clOrdId": cl_id, "side": "buy",
            "ordType": "post_only" if post_only else "limit", "px": str(preis), "sz": str(menge)})[0]

    def verkaufen_market(self, cl_id, menge):
        return self._req("POST", "/api/v5/trade/order", {
            "instId": INST, "tdMode": "cash", "clOrdId": cl_id, "side": "sell", "ordType": "market",
            "sz": str(menge), "tgtCcy": "base_ccy"})[0]

    def order(self, cl_id=None, ord_id=None):
        """-> dict mit state, accFillSz, avgPx, fee, feeCcy oder None wenn unbekannt."""
        q = f"clOrdId={cl_id}" if cl_id else f"ordId={ord_id}"
        try:
            d = self._req("GET", f"/api/v5/trade/order?instId={INST}&{q}")
        except OkxFehler as e:
            if "51603" in str(e):  # Order existiert nicht
                return None
            raise
        return d[0] if d else None

    def order_loeschen(self, cl_id):
        try:
            self._req("POST", "/api/v5/trade/cancel-order", {"instId": INST, "clOrdId": cl_id})
        except OkxFehler as e:
            if not any(c in str(e) for c in ("51400", "51401", "51402", "51603")):  # schon erledigt / weg
                raise

    def oco_setzen(self, algo_cl_id, menge, stop, ziel):
        """Stop und Ziel zusammen bei OKX. Beide lösen als Market-Order aus (-1), damit nie eine
        Position ungeschützt bleibt (ein Limit-Ziel könnte nach Auslösung unausgeführt hängen)."""
        return self._req("POST", "/api/v5/trade/order-algo", {
            "instId": INST, "tdMode": "cash", "side": "sell", "ordType": "oco", "sz": str(menge),
            "algoClOrdId": algo_cl_id, "tpTriggerPx": str(ziel), "tpOrdPx": "-1",
            "slTriggerPx": str(stop), "slOrdPx": "-1"})[0]

    def oco(self, algo_cl_id):
        """-> dict mit state (live/effective/canceled/order_failed), actualSide, ordId, algoId oder None."""
        for pfad in (f"/api/v5/trade/order-algo?algoClOrdId={algo_cl_id}",):
            try:
                d = self._req("GET", pfad)
                return d[0] if d else None
            except OkxFehler as e:
                if "51603" in str(e) or "51000" in str(e):
                    return None
                raise

    def oco_loeschen(self, algo_id):
        try:
            self._req("POST", "/api/v5/trade/cancel-algos", [{"instId": INST, "algoId": algo_id}])
        except OkxFehler as e:
            if not any(c in str(e) for c in ("51279", "51400", "51603")):
                raise
