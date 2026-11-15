# -*- coding: utf-8 -*-
"""
TradeLab V3 Auto-Bot für OKX Spot BTC-USDT – Strategie "Rücksetzer nur Long, Ziel 2R".

Ablauf pro Durchlauf (tick, jede Minute):
  1. Tagesplan (00:00 UTC) aus Binance-Tageskerzen berechnen
  2. Neue 5-Min-Kerzen: Limit schon vor 07:45 CH erreicht? -> "verpasst"
  3. 07:00 CH: Tagesplan per Telegram
  4. Ab 07:45 CH: Limit-Kauf bei OKX
  5. Kauf gefüllt -> sofort OCO (Stop + Ziel) bei OKX
  6. OCO ausgelöst -> Journal; 48 h vorbei -> OCO löschen, Market verkaufen
  7. Schutz: Not-Aus, 3 %-Rückgang, Position ohne OCO -> neu setzen
Jeder Durchlauf ist idempotent: Die Wahrheit kommt zuerst von OKX (clOrdId / algoClOrdId).
"""
import json, os, sys, time, traceback
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
from strategie import TAG, tagesplan, menge_berechnen, runde_ab, runde_preis, zeit_ch, ch_uhr_utc  # noqa: E402

STANDARD_CFG = {
    "strategie": "V3 Rücksetzer Long 2R (OKX)",
    "handel_aktiv": True,
    "risiko_prozent": 0.25,
    "max_offen": 2,
    "rueckgang_stopp_prozent": 3.0,
    "max_einsatz_usdt": 0,          # 0 = kein Limit (nur Demo); bei live Pflicht > 0
    "sma_tage": 20, "atr_tage": 14, "ruecksetzer_atr": 0.3, "stop_atr": 0.8, "ziel_r": 2.0,
    "max_stunden": 48, "start_ch": "07:45", "nachricht_ab_ch": "07:00",
    "pruefen_sekunden": 60,
}
JOURNAL_KOPF = ("eroeffnet_utc;geschlossen_utc;einstieg;stop;ziel;ausstieg;grund;menge;gebuehren_usdt;"
                "gewinn_usdt;r;konto_usdt;kauf_id;verkauf_id\n")


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


def fmt(x, n=2):
    return "-" if x is None else f"{x:,.{n}f}".replace(",", "'")


class Bot:
    def __init__(self, broker, daten, melder, ordner, cfg=None, trocken=False, uhr=time.time):
        self.b, self.d, self.m, self.ordner, self.trocken, self.uhr = broker, daten, melder, ordner, trocken, uhr
        self.cfg = dict(STANDARD_CFG)
        self.cfg.update(cfg or {})
        self.zpfad = os.path.join(ordner, "zustand.json")
        self.jpfad = os.path.join(ordner, "journal.csv")
        self.lpfad = os.path.join(ordner, "bot.log")
        self.z = self._lade()
        self.inst = None

    # ------------------------------------------------------------ Hilfen
    def _lade(self):
        try:
            with open(self.zpfad, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"plaene": {}, "trades": {}, "letzte_5m": 0, "hoch": 0, "halt": False}

    def speichern(self):
        tmp = self.zpfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.z, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.zpfad)

    def log(self, text):
        zeile = f"{iso(self.uhr())}  {text}"
        print(zeile, flush=True)
        try:
            if os.path.exists(self.lpfad) and os.path.getsize(self.lpfad) > 2_000_000:
                os.replace(self.lpfad, self.lpfad + ".1")
            with open(self.lpfad, "a", encoding="utf-8") as f:
                f.write(zeile + "\n")
        except OSError:
            pass

    def melde(self, titel, text=""):
        self.log(f"TELEGRAM {titel} | {text}".replace("\n", " / "))
        self.m(titel, text)

    # ------------------------------------------------------------ Hauptdurchlauf
    def tick(self):
        jetzt = self.uhr()
        if self.inst is None:
            self.inst = self.b.instrument()
        tag = int(jetzt) // TAG * TAG
        self._plan_holen(tag)
        self._kerzen_verarbeiten()
        konto = self._konto_pruefen()
        self._nachricht(tag, jetzt)
        self._kaufen(tag, jetzt, konto)
        self._kaeufe_abgleichen(jetzt)
        self._trades_verwalten(jetzt)
        self._wochenbericht(jetzt)
        self._aufraeumen(tag)
        self.z["letzter_lauf"] = int(jetzt)
        self.speichern()

    # 1 ----------------------------------------------------------
    def _plan_holen(self, tag):
        if str(tag) in self.z["plaene"]:
            return
        tage = self.d.tage(60)
        if not tage or tage[-1]["t"] != tag:
            return  # Tageskerze noch nicht da
        p = tagesplan(tage, self.cfg)
        self.z["plaene"][str(tag)] = p
        self.log(f"Tagesplan {iso(tag)}: " + (f"Limit {fmt(p['limit'])} Abstand {fmt(p['abstand'])}" if p["trend"]
                                               else "kein Trade (unter SMA)"))

    # 2 ----------------------------------------------------------
    def _kerzen_verarbeiten(self):
        letzte = self.z.get("letzte_5m") or int(self.uhr()) // 300 * 300 - 600
        for x in self.d.kerzen_5m(letzte):
            p = self.z["plaene"].get(str(x["t"] // TAG * TAG))
            if p and p["status"] in ("wartet", "order") and x["t"] < p["start"] and x["l"] <= p["limit"]:
                if p["status"] == "order":
                    self._kauf_abbrechen(p, "Preis war schon vor 07:45 unter dem Limit")
                p["status"] = "verpasst"
                self.log(f"Plan {iso(p['tag'])}: verpasst")
                if p.get("gemeldet"):
                    self.melde("⚠️ BTC: heute KEIN Trade",
                               f"Der Preis fiel schon vor {self.cfg['start_ch']} Uhr unter das Limit {fmt(p['limit'])}.")
            letzte = x["t"]
        self.z["letzte_5m"] = letzte

    # 3 ----------------------------------------------------------
    def _nachricht(self, tag, jetzt):
        p = self.z["plaene"].get(str(tag))
        if not p or p.get("gemeldet") or jetzt < p["nachricht"]:
            return
        p["gemeldet"] = True
        titel = f"📅 Tagesplan {zeit_ch(tag, '%d.%m.%Y')} · {self.cfg['strategie']}"
        if not p["trend"]:
            self.melde(titel, f"Heute KEIN Trade: Trend nach unten.\nGestern Schluss {fmt(p['gestern_schluss'])} "
                              f"liegt unter SMA {self.cfg['sma_tage']} ({fmt(p['sma'])}).")
        elif p["status"] == "verpasst":
            self.melde(titel, f"Heute KEIN Trade: Der Preis war schon in der Nacht unter dem Limit {fmt(p['limit'])}.")
        else:
            konto = self.z.get("konto", 0)
            menge, risiko = menge_berechnen(konto, p["abstand"], self.cfg, self.inst or {"lotSz": "0.00000001", "minSz": "0.00001"})
            self.melde(titel, f"Kauf-Limit: {fmt(p['limit'])}\nStop: {fmt(p['limit'] - p['abstand'])}\n"
                              f"Ziel (2R): {fmt(p['limit'] + self.cfg['ziel_r'] * p['abstand'])}\n"
                              f"Menge ca.: {fmt(menge or 0, 5)} BTC · Risiko {fmt(risiko)} USDT\n"
                              f"Order wird um {self.cfg['start_ch']} automatisch gesetzt.\n"
                              f"Konto {fmt(konto)} USDT · offen: {self._offen_anzahl()}")

    # 4 ----------------------------------------------------------
    def _konto_pruefen(self):
        usdt, btc, gesamt = self.b.guthaben()
        self.z["konto"] = gesamt
        self.z["hoch"] = max(self.z.get("hoch") or 0, gesamt)
        grenze = self.z["hoch"] * (1 - self.cfg["rueckgang_stopp_prozent"] / 100)
        if gesamt < grenze and not self.z.get("halt"):
            self.z["halt"] = True
            self.melde("🚨 NOT-AUS: 3 % unter Höchststand",
                       f"Konto {fmt(gesamt)} USDT, Höchststand {fmt(self.z['hoch'])}. Keine neuen Trades mehr. "
                       f"Offene Trades behalten ihren Stop. Freigabe: 'halt' in zustand.json auf false setzen.")
        return dict(usdt=usdt, btc=btc, gesamt=gesamt)

    def _offen_anzahl(self):
        return sum(1 for t in self.z["trades"].values() if t["status"] == "offen")

    def _kaufen(self, tag, jetzt, konto):
        p = self.z["plaene"].get(str(tag))
        if not p or p["status"] != "wartet" or jetzt < p["start"] or jetzt >= p["ende"]:
            return
        if self.z["letzte_5m"] < p["start"] - 300:
            return  # Kerzen vor 07:45 noch nicht alle geprüft
        if not self.cfg["handel_aktiv"] or self.z.get("halt"):
            p["status"] = "gesperrt"
            self.melde("⛔ Heute kein Trade", "Not-Aus aktiv (handel_aktiv=false oder 3 %-Rückgang).")
            return
        if self._offen_anzahl() >= self.cfg["max_offen"]:
            p["status"] = "voll"
            self.melde("ℹ️ Heute kein Trade", f"Schon {self.cfg['max_offen']} Trades offen.")
            return
        preis_jetzt = self.b.preis()
        limit = runde_preis(p["limit"], self.inst["tickSz"])
        max_e = self.cfg.get("max_einsatz_usdt") or None
        menge, risiko = menge_berechnen(konto["gesamt"], p["abstand"], self.cfg, self.inst, max_e, limit)
        if menge is None:
            p["status"] = "zu_klein"
            self.melde("⚠️ Konto zu klein", f"Menge unter Minimum {self.inst['minSz']} BTC.")
            return
        if menge * limit > konto["usdt"] * 0.995:
            menge = runde_ab(konto["usdt"] * 0.995 / limit, self.inst["lotSz"])
        cl = "v3" + datetime.fromtimestamp(tag, timezone.utc).strftime("%Y%m%d") + "buy"
        post_only = preis_jetzt > limit
        p.update(cl_buy=cl, menge=menge, risiko=risiko, limit_order=limit)
        if self.trocken:
            p["status"] = "order"
            self.log(f"TROCKEN: würde kaufen {menge} @ {limit}")
            return
        vorhanden = self.b.order(cl_id=cl)
        if not vorhanden:
            self.b.kaufen_limit(cl, limit, menge, post_only=post_only)
        p["status"] = "order"
        self.melde("🟢 Kauf-Order gesetzt", f"Limit {fmt(limit)} · Menge {fmt(menge, 5)} BTC · Risiko {fmt(risiko)} USDT\n"
                                            f"Gültig bis {zeit_ch(p['ende'])} Uhr.")

    def _kauf_abbrechen(self, p, grund):
        if not self.trocken and p.get("cl_buy"):
            self.b.order_loeschen(p["cl_buy"])
            o = self.b.order(cl_id=p["cl_buy"])
            if o and float(o.get("accFillSz") or 0) > 0:
                self._trade_eroeffnen(p, o)
                return
        self.melde("⚠️ Kauf-Order gelöscht", grund)

    # 5 ----------------------------------------------------------
    def _kaeufe_abgleichen(self, jetzt):
        for p in self.z["plaene"].values():
            if p["status"] != "order" or self.trocken:
                continue
            o = self.b.order(cl_id=p["cl_buy"])
            if o is None:
                continue
            gefuellt = float(o.get("accFillSz") or 0)
            if o["state"] == "filled":
                self._trade_eroeffnen(p, o)
            elif jetzt >= p["ende"]:
                self.b.order_loeschen(p["cl_buy"])
                o = self.b.order(cl_id=p["cl_buy"]) or o
                if float(o.get("accFillSz") or 0) > 0:
                    self._trade_eroeffnen(p, o)
                else:
                    p["status"] = "verfallen"
                    self.log(f"Plan {iso(p['tag'])}: Limit nicht erreicht, Order gelöscht")
            elif o["state"] == "canceled" and gefuellt == 0:
                p["status"] = "abgelehnt"
                self.melde(f"⚠️ Kauf-Order von {self.cfg.get('broker_name', 'Broker')} abgelehnt/gelöscht", "Heute kein Trade.")

    def _trade_eroeffnen(self, p, o):
        gefuellt = float(o["accFillSz"])
        gebuehr = float(o.get("fee") or 0)  # negativ
        netto = gefuellt + (gebuehr if o.get("feeCcy") == "BTC" else 0)
        netto = runde_ab(netto, self.inst["lotSz"])
        einstieg = float(o["avgPx"])
        stop = runde_preis(einstieg - p["abstand"], self.inst["tickSz"])
        ziel = runde_preis(einstieg + self.cfg["ziel_r"] * p["abstand"], self.inst["tickSz"])
        tag = str(p["tag"])
        t = dict(status="offen", ts=int(o.get("fillTime") or o.get("uTime") or self.uhr() * 1000) // 1000,
                 einstieg=einstieg, menge=netto, menge_brutto=gefuellt, stop=stop, ziel=ziel,
                 risiko=p["abstand"] * netto, gebuehr_kauf_usdt=abs(gebuehr) * (einstieg if o.get("feeCcy") == "BTC" else 1),
                 kauf_id=o.get("ordId"), algo_cl="v3" + datetime.fromtimestamp(p["tag"], timezone.utc).strftime("%Y%m%d") + "oco")
        self.z["trades"][tag] = t
        p["status"] = "gekauft"
        self._oco_sicherstellen(t)
        self.melde("🟢 GEKAUFT BTC", f"Einstieg {fmt(einstieg)} · Menge {fmt(netto, 5)} BTC\nStop {fmt(stop)} · Ziel {fmt(ziel)} "
                                     f"(Stop + Ziel bei {self.cfg.get('broker_name', 'Broker')} ✅)\nSpätester Verkauf: {zeit_ch(t['ts'] + self.cfg['max_stunden'] * 3600)} Uhr")

    def _oco_sicherstellen(self, t):
        o = self.b.oco(t["algo_cl"])
        if o and o.get("state") in ("live", "effective", "partially_effective"):
            t["algo_id"] = o["algoId"]
            return o
        if o and o.get("state") in ("canceled", "order_failed"):
            t["algo_cl"] = t["algo_cl"][:-3] + "o" + str(int(self.uhr()) % 100000)  # neue eindeutige ID
            self.melde("🚨 Position ohne Schutz", "OCO war gelöscht/fehlgeschlagen – wird neu gesetzt.")
        r = self.b.oco_setzen(t["algo_cl"], t["menge"], t["stop"], t["ziel"])
        t["algo_id"] = r.get("algoId")
        return None

    # 6 ----------------------------------------------------------
    def _trades_verwalten(self, jetzt):
        for tag, t in self.z["trades"].items():
            if self.trocken:
                continue
            if t["status"] == "48h_verkauf":
                v = self.b.order(cl_id=t["cl_48h"])
                if v is None:
                    _, btc, _ = self.b.guthaben()
                    self.b.verkaufen_market(t["cl_48h"], runde_ab(min(t["menge"], btc), self.inst["lotSz"]))
                    v = self.b.order(cl_id=t["cl_48h"])
                if v and v.get("state") == "filled":
                    self._trade_schliessen(t, v, "48h")
                continue
            if t["status"] != "offen":
                continue
            o = self._oco_sicherstellen(t)
            if o and o.get("state") == "effective":
                v = self.b.order(ord_id=o["ordId"]) if o.get("ordId") else None
                if v and v.get("state") == "filled":
                    self._trade_schliessen(t, v, "Ziel" if o.get("actualSide") == "tp" else "Stop")
            elif jetzt >= t["ts"] + self.cfg["max_stunden"] * 3600:
                self.b.oco_loeschen(t["algo_id"])
                o2 = self.b.oco(t["algo_cl"])
                if o2 and o2.get("state") == "effective":
                    continue  # kurz vorher ausgelöst -> nächster Durchlauf schliesst
                t["status"] = "48h_verkauf"
                t["cl_48h"] = "v3" + datetime.fromtimestamp(int(tag), timezone.utc).strftime("%Y%m%d") + "z48"
                _, btc, _ = self.b.guthaben()
                self.b.verkaufen_market(t["cl_48h"], runde_ab(min(t["menge"], btc), self.inst["lotSz"]))
                v = self.b.order(cl_id=t["cl_48h"])
                if v and v.get("state") == "filled":
                    self._trade_schliessen(t, v, "48h")

    def _trade_schliessen(self, t, v, grund):
        menge = float(v["accFillSz"])
        preis = float(v["avgPx"])
        gebuehr = abs(float(v.get("fee") or 0)) * (preis if v.get("feeCcy") == "BTC" else 1)
        gewinn = (preis - t["einstieg"]) * menge - gebuehr - t["gebuehr_kauf_usdt"]
        r = gewinn / t["risiko"] if t["risiko"] else 0
        t.update(status="zu", grund=grund, ausstieg=preis, gewinn=gewinn, r=r, ts_zu=int(self.uhr()))
        usdt, btc, gesamt = self.b.guthaben()
        neu = not os.path.exists(self.jpfad)
        with open(self.jpfad, "a", encoding="utf-8") as f:
            if neu:
                f.write(JOURNAL_KOPF)
            f.write(f"{iso(t['ts'])};{iso(t['ts_zu'])};{t['einstieg']:.1f};{t['stop']:.1f};{t['ziel']:.1f};{preis:.1f};"
                    f"{grund};{menge:.8f};{gebuehr + t['gebuehr_kauf_usdt']:.4f};{gewinn:.4f};{r:.3f};{gesamt:.2f};"
                    f"{t.get('kauf_id')};{v.get('ordId')}\n")
        z = {"Ziel": "✅", "Stop": "❌", "48h": "⏰"}[grund]
        self.melde(f"{z} BTC geschlossen: {grund}", f"Einstieg {fmt(t['einstieg'])} → Ausstieg {fmt(preis)}\n"
                                                    f"Ergebnis {gewinn:+.2f} USDT ({r:+.2f} R) · Konto {fmt(gesamt)} USDT")

    # 7 ----------------------------------------------------------
    def _wochenbericht(self, jetzt):
        d = datetime.fromtimestamp(jetzt, timezone.utc)
        woche = d.strftime("%G-W%V")
        if d.weekday() != 6 or jetzt < ch_uhr_utc(int(jetzt) // TAG * TAG, "19:00") or self.z.get("bericht_woche") == woche:
            return
        self.z["bericht_woche"] = woche
        if os.path.exists(self.jpfad):
            with open(self.jpfad, encoding="utf-8") as f:
                zeilen = [z.strip().split(";") for z in f.readlines()[1:] if z.strip()]
            g = [float(z[9]) for z in zeilen]
            rs = [float(z[10]) for z in zeilen]
        else:
            g, rs = [], []
        gew, ver = sum(x for x in g if x > 0), -sum(x for x in g if x <= 0)
        if g:
            text = (f"Trades: {len(g)} · Treffer: {sum(x > 0 for x in g) / len(g) * 100:.0f} %\n"
                    + (f"Profit-Faktor: {gew / ver:.2f}\n" if ver else "")
                    + f"Summe: {sum(rs):+.2f} R · {sum(g):+.2f} USDT\n")
        else:
            text = "Noch keine abgeschlossenen Trades.\n"
        text += (f"Konto {fmt(self.z.get('konto'))} USDT · offen: {self._offen_anzahl()}\n"
                 f"Backtest-Erwartung: ca. 54 % Treffer, PF ca. 1,3")
        self.melde(f"📊 Wochenbericht · {self.cfg['strategie']}", text)

    def _aufraeumen(self, tag):
        for k in list(self.z["plaene"]):
            if int(k) < tag - 10 * TAG and self.z["plaene"][k]["status"] not in ("order",):
                del self.z["plaene"][k]
        for k in list(self.z["trades"]):
            t = self.z["trades"][k]
            if t["status"] == "zu" and t.get("ts_zu", 0) < self.uhr() - 30 * TAG:
                del self.z["trades"][k]


# ---------------------------------------------------------------- Start (Server)
def lade_env(pfad):
    werte = {k: v for k, v in os.environ.items() if k.startswith(("OKX_", "ALPACA_", "TELEGRAM_", "LIVE_", "BROKER"))}
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as f:
            for z in f:
                z = z.strip()
                if z and not z.startswith("#") and "=" in z:
                    k, v = z.split("=", 1)
                    werte[k.strip()] = v.strip().strip('"').strip("'")
    return werte


def telegram_melder(token, chat):
    import urllib.parse, urllib.request

    def senden(titel, text=""):
        if not (token and chat):
            return
        try:
            daten = urllib.parse.urlencode({"chat_id": chat, "text": f"{titel}\n{text}".strip()}).encode()
            urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=daten),
                                   timeout=20).read()
        except Exception as e:  # Telegram darf den Bot nie stoppen
            print(f"Telegram-Fehler: {e}", flush=True)
    return senden


def main():
    from okx import Okx
    from daten import Daten
    env = lade_env(os.environ.get("TRADELAB_ENV", os.path.expanduser("~/.tradelab.env")))
    ordner = os.environ.get("TRADELAB_DATEN", os.path.join(HIER, "daten"))
    os.makedirs(ordner, exist_ok=True)
    cfg = dict(STANDARD_CFG)
    try:
        with open(os.path.join(HIER, "einstellungen.json"), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    modus = cfg.get("modus", "demo")
    if modus == "live":
        if env.get("LIVE_BESTAETIGT") != "JA" or not cfg.get("max_einsatz_usdt"):
            print("Live-Modus verweigert: LIVE_BESTAETIGT=JA in .env und max_einsatz_usdt > 0 nötig.")
            sys.exit(1)
    melder = telegram_melder(env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    daten = Daten(melden=lambda t: melder(t))
    trocken = "--trocken" in sys.argv
    broker_name = env.get("BROKER", cfg.get("broker", "okx"))
    if broker_name == "alpaca":
        from alpaca import Alpaca
        if modus == "live":
            print("Alpaca live ist nicht vorgesehen (Gebühren zu hoch). Abbruch.")
            sys.exit(1)
        basis = (env.get("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").strip()
        if "paper-api." not in basis:
            print("Nur Alpaca PAPER erlaubt (ALPACA_BASE_URL muss paper-api sein). Abbruch.")
            sys.exit(1)
        broker = Alpaca(env.get("ALPACA_KEY_ID", "").strip(), env.get("ALPACA_SECRET_KEY", "").strip(), basis, daten=daten)
        cfg["strategie"] = "V3 Rücksetzer Long 2R (Alpaca Paper)"
        cfg["broker_name"] = "Alpaca"
    else:
        cfg["broker_name"] = "OKX"
        broker = Okx(env.get("OKX_API_KEY", ""), env.get("OKX_SECRET", ""), env.get("OKX_PASSPHRASE", ""), demo=(modus != "live"))
        if trocken and not env.get("OKX_API_KEY"):
            class OhneKonto(Okx):  # Trockenlauf ohne Schlüssel: nur öffentliche Daten, Spielkonto 5000 USDT
                def guthaben(self):
                    return 5000.0, 0.0, 5000.0
            broker = OhneKonto("", "", "", demo=True)
    bot = Bot(broker, daten, melder, ordner, cfg, trocken=trocken)
    if broker_name == "alpaca":
        broker.z = bot.z.setdefault("broker", {})
    if "--test-order" in sys.argv:
        return test_order(broker, melder, cfg.get("broker_name", "Broker"))
    bot.log(f"Bot gestartet · {broker_name} · Modus {modus}{' · TROCKEN' if trocken else ''}")
    if "--einmal" not in sys.argv:
        melder("🤖 TradeLab Auto-Bot gestartet", f"{broker_name} · Modus: {modus}{' (trocken)' if trocken else ''}")
    while True:
        try:
            bot.tick()
            bot.z["fehler_in_folge"] = 0
        except Exception as e:
            bot.z["fehler_in_folge"] = bot.z.get("fehler_in_folge", 0) + 1
            n = bot.z["fehler_in_folge"]
            bot.log(f"FEHLER: {e}\n{traceback.format_exc()}")
            if n in (3, 30, 300):
                melder("🚨 Bot-Fehler", f"{n}x hintereinander: {str(e)[:300]}")
            try:
                bot.speichern()
            except Exception:
                pass
        if "--einmal" in sys.argv:
            break
        time.sleep(cfg["pruefen_sekunden"])

def test_order(broker, melder, name="Broker"):
    """Demo-Test: Konto lesen, Limit-Kauf weit unter Markt setzen und löschen."""
    k, s = broker.key if hasattr(broker, "key") else "", getattr(broker, "secret", "")
    print(f"Diagnose: KEY_ID Länge {len(k)}, beginnt mit PK: {k.startswith('PK')} · SECRET Länge {len(s)} · URL {getattr(broker, 'basis', '')}")
    usd, btc, gesamt = broker.guthaben()
    print(f"Konto ok: frei {usd:.2f} · BTC {btc} · gesamt {gesamt:.2f}")
    preis = broker.preis()
    inst = broker.instrument()
    px = runde_preis(preis * 0.5, inst["tickSz"])
    cl = "v3test" + str(int(time.time()))
    menge = max(float(inst["minSz"]) * 10, 0.0005)
    broker.kaufen_limit(cl, px, menge, post_only=True)
    o = broker.order(cl_id=cl)
    broker.order_loeschen(cl)
    o2 = broker.order(cl_id=cl)
    text = (f"Konto gesamt {gesamt:,.2f} · Kurs {preis:,.2f}\n"
            f"Order gesetzt: {o and o.get('state')} · danach: {o2 and o2.get('state')}")
    print(text)
    melder(f"🧪 {name}-Test", text)


if __name__ == "__main__":
    main()
