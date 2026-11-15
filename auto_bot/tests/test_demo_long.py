# -*- coding: utf-8 -*-
"""Ablauf-Test Demo-Long: Bot + (gefälschtes) Alpaca + gemeinsames Konto. Ohne Netzwerk."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot import Bot
from p4_demo import Konto, OKX_MAKER, OKX_TAKER
from test_alpaca import FakeAlpaca
from strategie import ch_uhr_utc

TAG = 86400
heute = 1794700800  # 15.11.2026 00:00 UTC
uhr = [heute + 9 * 3600]

class D:
    """60 Tage Aufwärtstrend, heute Eröffnung 110; 5-Min-Kerzen frei steuerbar."""
    def __init__(self):
        self.k = []
    def tage(self, n):
        t = [dict(t=heute - (60 - i) * TAG, o=50 + i, h=52 + i, l=48 + i, c=50 + i, fertig=True) for i in range(60)]
        return t[-(n - 1):] + [dict(t=heute, o=110, h=111, l=109, c=110, fertig=False)]
    def kerzen_5m(self, seit):
        return [x for x in self.k if x["t"] > seit and x["t"] + 300 <= uhr[0]]

d = D()
br = FakeAlpaca(d); br.symbol, br.pos, br.maker, br.taker = "BTC/USD", "BTCUSD", OKX_MAKER, OKX_TAKER
br.cash = 100000
tmp = tempfile.mkdtemp(); konto = Konto(os.path.join(tmp, "k"))
meld = []
cfg = dict(name="BTC Long", coin="BTC", id_prefix="t", sma_tage=50, risiko_prozent=3.0, rueckgang_stopp_prozent=99,
           ende_utc_stunde=20, nur_trades=True, broker_name="Alpaca")
bot = Bot(br, d, lambda t, x="": meld.append(t + " | " + x.replace("\n", " / ")), tmp, cfg, konto_fn=konto.wert, buchung=konto.buchen)
br.z = bot.z.setdefault("broker", {}); bot.log = lambda t: None
br.preis = lambda: 110.0
start = ch_uhr_utc(heute, "07:45")
d.k = [dict(t=x, o=110, h=110.5, l=109.5, c=110, fertig=True) for x in range(heute, start + 3600, 300)]
bot.tick()
p = bot.z["plaene"][str(heute)]
assert p["trend"] and p["status"] == "order", p["status"]
assert p["ende"] == heute + 20 * 3600
# Kauf füllt bei Alpaca
o = br._roh(cl_id=p["cl_buy"]); o.update(status="filled", filled_qty=o["qty"], filled_avg_price=str(p["limit_order"]), filled_at="2026-11-15T10:00:00Z")
br.btc = float(o["qty"])
uhr[0] += 600; bot.tick()
t = list(bot.z["trades"].values())[0]
assert t["status"] == "offen" and abs(t["risiko"] - 3.0) < 0.2, t
# Ziel erreicht (5-Min-Hoch über Ziel). Adapter rechnet mit echter Uhr -> Prüfzeitpunkt auf Testzeit setzen
br.z["ziele"][t["algo_cl"]]["geprueft"] = uhr[0] - 300
d.k.append(dict(t=uhr[0], o=110, h=t["ziel"] + 1, l=109, c=110, fertig=True))
uhr[0] += 900; bot.tick(); bot.tick()
t = list(bot.z["trades"].values())[0]
assert t["status"] == "zu" and t["grund"] == "Ziel", t
print("\n".join(meld))
print("Konto:", round(konto.wert(), 2))
assert konto.wert() > 100
print("OK test_demo_long")
