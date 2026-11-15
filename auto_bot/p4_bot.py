# -*- coding: utf-8 -*-
"""
TradeLab P4 – Multi-Strategie-Bot als Simulation mit Live-Kursen (gratis auf GitHub).

5 Strategien (gleiche Rücksetzer-Logik wie V3, Backtest 2022–2026):
  BTC Long (SMA 50) · ETH Long (SMA 50) · ETH Short (SMA 20) · ADA Short (SMA 20) · SOL Long (SMA 20)

Regeln pro Strategie:
  - Trend: Long nur wenn Vortages-Schluss > SMA, Short nur wenn < SMA
  - Limit = Tages-Eröffnung (00:00 UTC) ∓ 0.3 × ATR14
  - Order aktiv ab 07:45 CH bis 20:00 UTC; Limit vor 07:45 schon erreicht -> "verpasst"
  - Stop 0.8 × ATR, Ziel 2R, spätestens nach 48 h raus; max 2 offen pro Strategie
Simulation: Der Bot liest abgeschlossene 5-Min-Kerzen von Binance und "füllt" Orders genau
wie im Backtest (Stop vor Ziel, wenn beides in einer Kerze). Gebühren wie OKX-Perpetual (0.09 % hin+zurück).
Kein Broker, keine Schlüssel nötig – nur Telegram.
"""
import json, os, sys, time, traceback
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
from strategie import TAG, zeit_ch, ch_uhr_utc  # noqa: E402

STRATEGIEN = [
    dict(name="BTC Long", symbol="BTCUSDT", seite=1, sma=50),
    dict(name="ETH Long", symbol="ETHUSDT", seite=1, sma=50),
    dict(name="ETH Short", symbol="ETHUSDT", seite=-1, sma=20),
    dict(name="ADA Short", symbol="ADAUSDT", seite=-1, sma=20),
    dict(name="SOL Long", symbol="SOLUSDT", seite=1, sma=20),
]
STANDARD_CFG = {
    "start_kapital": 100.0,
    "risiko_prozent": 3.0,          # pro Trade, vom aktuellen Konto
    "gebuehr_rt": 0.0009,           # OKX Perpetual hin+zurück
    "atr_tage": 14, "ruecksetzer_atr": 0.3, "stop_atr": 0.8, "ziel_r": 2.0,
    "max_stunden": 48, "max_offen_pro_strategie": 2, "max_offen_gesamt": 99,
    "start_ch": "07:45", "nachricht_ab_ch": "07:00", "ende_utc_stunde": 20,
    "rueckgang_stopp_prozent": 40.0,  # Not-Aus: keine neuen Trades unter Höchststand − x %
    "handel_aktiv": True,
}
KOPF = "strategie;richtung;eroeffnet_utc;geschlossen_utc;einstieg;stop;ziel;ausstieg;grund;r;gewinn;konto\n"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def fmt(x, n=2):
    if x is None:
        return "-"
    if abs(x) < 10 and n == 2:
        n = 4
    return f"{x:,.{n}f}".replace(",", "'")


class P4Bot:
    def __init__(self, daten_fuer, melder, ordner, cfg=None, uhr=time.time):
        self.daten_fuer, self.m, self.ordner, self.uhr = daten_fuer, melder, ordner, uhr
        self.cfg = dict(STANDARD_CFG); self.cfg.update(cfg or {})
        self.zpfad = os.path.join(ordner, "zustand.json")
        self.jpfad = os.path.join(ordner, "journal.csv")
        os.makedirs(ordner, exist_ok=True)
        self.z = self._lade()

    # ------------------------------------------------------------ Grundlagen
    def _lade(self):
        try:
            with open(self.zpfad, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            k = self.cfg["start_kapital"]
            return {"konto": k, "hoch": k, "halt": False, "plaene": {}, "trades": [], "letzte_5m": {}, "nr": 0}

    def speichern(self):
        tmp = self.zpfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.z, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.zpfad)

    def log(self, t):
        print(f"{iso(self.uhr())}  {t}", flush=True)

    def melde(self, titel, text=""):
        self.log(f"TELEGRAM {titel} | {text}".replace("\n", " / "))
        self.m(titel, text)

    def offen(self, name=None):
        return [t for t in self.z["trades"] if t["status"] == "offen" and (name is None or t["s"] == name)]

    # ------------------------------------------------------------ Durchlauf
    def tick(self):
        jetzt = self.uhr()
        tag = int(jetzt) // TAG * TAG
        tage_cache, kerzen_cache = {}, {}
        for s in STRATEGIEN:
            sym = s["symbol"]
            if sym not in tage_cache:
                tage_cache[sym] = self.daten_fuer(sym).tage(60)
            self._plan(s, tag, tage_cache[sym])
        for sym in sorted({s["symbol"] for s in STRATEGIEN}):
            letzte = self.z["letzte_5m"].get(sym) or tag - 300  # Erststart: ganzen Tag nachspielen (verpasst-Regel)
            kerzen = self.daten_fuer(sym).kerzen_5m(letzte)
            for k in kerzen:
                self._kerze(sym, k)
                letzte = k["t"]
            self.z["letzte_5m"][sym] = letzte
        self._tagesnachricht(tag, jetzt)
        self._wochenbericht(jetzt)
        self._aufraeumen(tag)
        self.z["letzter_lauf"] = int(jetzt)
        self.speichern()

    def _plan(self, s, tag, tage):
        key = f"{s['name']}|{tag}"
        if key in self.z["plaene"] or not tage or tage[-1]["t"] != tag:
            return
        alt = [x for x in tage[:-1] if x["fertig"]]
        n_sma, n_atr = s["sma"], self.cfg["atr_tage"]
        if len(alt) < max(n_sma, n_atr) + 1:
            return
        sma = sum(x["c"] for x in alt[-n_sma:]) / n_sma
        trs = [max(alt[i]["h"] - alt[i]["l"], abs(alt[i]["h"] - alt[i - 1]["c"]), abs(alt[i]["l"] - alt[i - 1]["c"]))
               for i in range(len(alt) - n_atr, len(alt))]
        atr = sum(trs) / n_atr
        schluss, eroeff = alt[-1]["c"], tage[-1]["o"]
        trend = schluss > sma if s["seite"] == 1 else schluss < sma
        p = dict(s=s["name"], sym=s["symbol"], seite=s["seite"], tag=tag, trend=trend, sma=sma, schluss=schluss,
                 limit=eroeff - s["seite"] * self.cfg["ruecksetzer_atr"] * atr, abstand=self.cfg["stop_atr"] * atr,
                 start=ch_uhr_utc(tag, self.cfg["start_ch"]), ende=tag + self.cfg["ende_utc_stunde"] * 3600,
                 status="wartet" if trend else "kein_trade")
        self.z["plaene"][key] = p
        self.log(f"Plan {key}: " + (f"Limit {fmt(p['limit'])}" if trend else "kein Trade (Trend)"))

    def _tagesnachricht(self, tag, jetzt):
        if self.z.get("gemeldet_tag") == tag or jetzt < ch_uhr_utc(tag, self.cfg["nachricht_ab_ch"]):
            return
        plaene = [self.z["plaene"].get(f"{s['name']}|{tag}") for s in STRATEGIEN]
        if any(p is None for p in plaene):
            return  # noch nicht alle Tageskerzen da
        self.z["gemeldet_tag"] = tag
        zeilen = []
        for p in plaene:
            pfeil = "🟢" if p["seite"] == 1 else "🔴"
            if p["status"] == "kein_trade":
                zeilen.append(f"⚪ {p['s']}: kein Trade (Trend passt nicht)")
            elif p["status"] == "verpasst":
                zeilen.append(f"⚪ {p['s']}: verpasst (Limit schon in der Nacht erreicht)")
            else:
                ziel = p["limit"] + p["seite"] * self.cfg["ziel_r"] * p["abstand"]
                stop = p["limit"] - p["seite"] * p["abstand"]
                zeilen.append(f"{pfeil} {p['s']}: Limit {fmt(p['limit'])} · Stop {fmt(stop)} · Ziel {fmt(ziel)}")
        self.melde(f"📅 P4 Tagesplan {zeit_ch(tag, '%d.%m.%Y')} (Simulation)",
                   "\n".join(zeilen) + f"\nOrders aktiv {self.cfg['start_ch']}–{zeit_ch(tag + self.cfg['ende_utc_stunde'] * 3600, '%H:%M')} Uhr"
                   f"\nKonto {fmt(self.z['konto'])} · offen: {len(self.offen())}")

    # ------------------------------------------------------------ Kerzen-Logik (wie Backtest)
    def _kerze(self, sym, k):
        ende_k = k["t"] + 300
        # 1) offene Trades dieses Symbols
        for t in [t for t in self.offen() if t["sym"] == sym and k["t"] >= t["ab"]]:
            sd = t["seite"]
            if (sd == 1 and k["l"] <= t["stop"]) or (sd == -1 and k["h"] >= t["stop"]):
                self._schliessen(t, t["stop"], "Stop", ende_k)
            elif (sd == 1 and k["h"] >= t["ziel"]) or (sd == -1 and k["l"] <= t["ziel"]):
                self._schliessen(t, t["ziel"], "Ziel", ende_k)
            elif ende_k >= t["ts"] + self.cfg["max_stunden"] * 3600:
                self._schliessen(t, k["c"], "48h", ende_k)
        # 2) Plan des Tages
        for s in [s for s in STRATEGIEN if s["symbol"] == sym]:
            p = self.z["plaene"].get(f"{s['name']}|{k['t'] // TAG * TAG}")
            if not p or p["status"] != "wartet":
                continue
            beruehrt = k["l"] <= p["limit"] if p["seite"] == 1 else k["h"] >= p["limit"]
            if k["t"] < p["start"]:
                if beruehrt:
                    p["status"] = "verpasst"
                    if self.z.get("gemeldet_tag") == p["tag"]:
                        self.melde(f"⚪ {p['s']}: heute kein Trade", f"Limit {fmt(p['limit'])} schon vor {self.cfg['start_ch']} erreicht.")
                continue
            if k["t"] >= p["ende"]:
                p["status"] = "verfallen"
                continue
            if not beruehrt:
                continue
            if (len(self.offen(p["s"])) >= self.cfg["max_offen_pro_strategie"]
                    or len(self.offen()) >= self.cfg["max_offen_gesamt"]):
                p["status"] = "voll"
                continue
            if not self.cfg["handel_aktiv"] or self.z.get("halt"):
                p["status"] = "gesperrt"
                continue
            self._eroeffnen(p, k)

    def _eroeffnen(self, p, k):
        sd, e = p["seite"], p["limit"]
        risiko = self.z["konto"] * self.cfg["risiko_prozent"] / 100
        self.z["nr"] += 1
        t = dict(nr=self.z["nr"], s=p["s"], sym=p["sym"], seite=sd, ts=k["t"], ab=k["t"] + 300, einstieg=e,
                 stop=e - sd * p["abstand"], ziel=e + sd * self.cfg["ziel_r"] * p["abstand"], abstand=p["abstand"],
                 risiko=risiko, menge=risiko / p["abstand"], status="offen")
        self.z["trades"].append(t)
        p["status"] = "gekauft"
        self.melde(f"{'🟢 LONG' if sd == 1 else '🔴 SHORT'} {p['s']} eröffnet (Sim)",
                   f"Einstieg {fmt(e)} · Stop {fmt(t['stop'])} · Ziel {fmt(t['ziel'])}\n"
                   f"Risiko {fmt(risiko)} ({self.cfg['risiko_prozent']} %) · spätestens {zeit_ch(t['ts'] + self.cfg['max_stunden'] * 3600)} Uhr raus")
        # Einstiegskerze: nur Stop prüfen (konservativ, wie Backtest)
        if (sd == 1 and k["l"] <= t["stop"]) or (sd == -1 and k["h"] >= t["stop"]):
            self._schliessen(t, t["stop"], "Stop", k["t"] + 300)

    def _schliessen(self, t, preis, grund, ts):
        r = t["seite"] * (preis - t["einstieg"]) / t["abstand"] - self.cfg["gebuehr_rt"] * t["einstieg"] / t["abstand"]
        gewinn = r * t["risiko"]
        self.z["konto"] += gewinn
        self.z["hoch"] = max(self.z["hoch"], self.z["konto"])
        t.update(status="zu", ausstieg=preis, grund=grund, r=r, gewinn=gewinn, ts_zu=ts)
        neu = not os.path.exists(self.jpfad)
        with open(self.jpfad, "a", encoding="utf-8") as f:
            if neu:
                f.write(KOPF)
            f.write(f"{t['s']};{'Long' if t['seite'] == 1 else 'Short'};{iso(t['ts'])};{iso(ts)};{t['einstieg']:.6g};"
                    f"{t['stop']:.6g};{t['ziel']:.6g};{preis:.6g};{grund};{r:.3f};{gewinn:.4f};{self.z['konto']:.4f}\n")
        icon = {"Ziel": "✅", "Stop": "❌", "48h": "⏰"}[grund]
        self.melde(f"{icon} {t['s']} geschlossen: {grund} (Sim)",
                   f"{fmt(t['einstieg'])} → {fmt(preis)} · {r:+.2f} R · {gewinn:+.2f}\nKonto {fmt(self.z['konto'])}")
        grenze = self.z["hoch"] * (1 - self.cfg["rueckgang_stopp_prozent"] / 100)
        if self.z["konto"] < grenze and not self.z.get("halt"):
            self.z["halt"] = True
            self.melde("🚨 NOT-AUS", f"Konto {fmt(self.z['konto'])} ist mehr als {self.cfg['rueckgang_stopp_prozent']} % "
                                    f"unter dem Höchststand {fmt(self.z['hoch'])}. Keine neuen Trades.")

    # ------------------------------------------------------------ Bericht / Aufräumen
    def _wochenbericht(self, jetzt):
        d = datetime.fromtimestamp(jetzt, timezone.utc)
        woche = d.strftime("%G-W%V")
        if d.weekday() != 6 or jetzt < ch_uhr_utc(int(jetzt) // TAG * TAG, "19:00") or self.z.get("bericht_woche") == woche:
            return
        self.z["bericht_woche"] = woche
        zeilen = []
        try:
            with open(self.jpfad, encoding="utf-8") as f:
                rows = [z.strip().split(";") for z in f.readlines()[1:] if z.strip()]
        except FileNotFoundError:
            rows = []
        for s in STRATEGIEN:
            rs = [float(z[9]) for z in rows if z[0] == s["name"]]
            if rs:
                g, v = sum(x for x in rs if x > 0), -sum(x for x in rs if x <= 0)
                pf = f"PF {g / v:.2f}" if v else "PF –"
                zeilen.append(f"{s['name']}: {len(rs)} Trades · {sum(x > 0 for x in rs)}/{len(rs)} Gewinner · {sum(rs):+.2f} R · {pf}")
            else:
                zeilen.append(f"{s['name']}: noch keine Trades")
        alle = [float(z[9]) for z in rows]
        start = self.cfg["start_kapital"]
        self.melde("📊 P4 Wochenbericht (Simulation)",
                   "\n".join(zeilen) + f"\nGesamt: {len(alle)} Trades · {sum(alle):+.2f} R\n"
                   f"Konto {fmt(self.z['konto'])} (Start {fmt(start)}, {(self.z['konto'] / start - 1) * 100:+.1f} %)\n"
                   f"Backtest-Erwartung: ca. 5 Trades/Woche, PF ca. 1.3")

    def _aufraeumen(self, tag):
        for k in list(self.z["plaene"]):
            if self.z["plaene"][k]["tag"] < tag - 5 * TAG:
                del self.z["plaene"][k]
        self.z["trades"] = [t for t in self.z["trades"] if t["status"] == "offen" or t.get("ts_zu", 0) > self.uhr() - 30 * TAG]


def main():
    from daten import Daten
    from bot import lade_env, telegram_melder
    env = lade_env(os.environ.get("TRADELAB_ENV", os.path.expanduser("~/.tradelab.env")))
    ordner = os.environ.get("TRADELAB_P4_DATEN", os.path.join(HIER, "daten_p4"))
    cfg = {}
    try:
        with open(os.path.join(HIER, "p4_einstellungen.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        pass
    melder = telegram_melder(env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    cache = {}

    def daten_fuer(sym):
        if sym not in cache:
            cache[sym] = Daten(melden=lambda t: melder(t), symbol=sym)
        return cache[sym]
    bot = P4Bot(daten_fuer, melder, ordner, cfg)
    try:
        bot.tick()
        bot.z["fehler_in_folge"] = 0
    except Exception as e:
        bot.z["fehler_in_folge"] = bot.z.get("fehler_in_folge", 0) + 1
        bot.log(f"FEHLER: {e}\n{traceback.format_exc()}")
        if bot.z["fehler_in_folge"] in (3, 30, 300):
            melder("🚨 P4-Bot-Fehler", f"{bot.z['fehler_in_folge']}x hintereinander: {str(e)[:300]}")
    bot.speichern()


if __name__ == "__main__":
    main()
