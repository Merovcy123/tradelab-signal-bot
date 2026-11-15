# -*- coding: utf-8 -*-
"""Kursdaten: Binance BTCUSDT (wie Backtest), Ersatz OKX BTC-USDT."""
import json, time, urllib.request

UA = {"User-Agent": "TradeLab-Bot"}


def _hole(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.loads(r.read().decode())


class Daten:
    def __init__(self, melden=None, symbol="BTCUSDT"):
        self.symbol = symbol
        self.okx_id = symbol.replace("USDT", "-USDT")
        self.quelle = "binance"
        self.melden = melden or (lambda t: None)

    def _binance(self, intervall, limit=None, start=None):
        q = f"symbol={self.symbol}&interval={intervall}&limit={limit or 1000}"
        if start:
            q += f"&startTime={int(start) * 1000}"
        roh = _hole(f"https://data-api.binance.vision/api/v3/klines?{q}")
        jetzt = int(time.time() * 1000)
        return [dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]),
                     fertig=int(k[6]) < jetzt) for k in roh]

    def _okx(self, bar, limit=300, start=None):
        # OKX liefert neueste zuerst; 1Dutc = Tageskerze ab 00:00 UTC
        roh = _hole(f"https://www.okx.com/api/v5/market/candles?instId={self.okx_id}&bar={bar}&limit={limit}")["data"]
        out = [dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]),
                    fertig=k[8] == "1") for k in reversed(roh)]
        return [x for x in out if not start or x["t"] >= start]

    def _mit_ersatz(self, f_binance, f_okx):
        try:
            d = f_binance()
            if self.quelle != "binance":
                self.quelle = "binance"
                self.melden(f"✅ {self.symbol}: Datenquelle wieder Binance.")
            return d
        except Exception as e:
            if self.quelle != "okx":
                self.quelle = "okx"
                self.melden(f"🚨 {self.symbol}: Binance-Daten nicht erreichbar ({e}). Ersatz: OKX-Kerzen.")
            return f_okx()

    def tage(self, anzahl=60):
        return self._mit_ersatz(lambda: self._binance("1d", anzahl), lambda: self._okx("1Dutc", anzahl))

    def kerzen_5m(self, seit):
        """Abgeschlossene 5-Min-Kerzen mit t > seit."""
        start = max(seit + 1, int(time.time()) - 80 * 3600)
        d = self._mit_ersatz(lambda: self._binance("5m", 1000, start), lambda: self._okx("5m", 300, start))
        return [x for x in d if x["fertig"] and x["t"] > seit]
