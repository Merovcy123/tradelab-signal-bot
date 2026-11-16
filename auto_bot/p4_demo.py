# -*- coding: utf-8 -*-
"""
TradeLab P4 – Demo-Betrieb (läuft alle 10 Min auf GitHub, 0 Fr.).

  Longs  (BTC, ETH, SOL): echte Orders im Alpaca-PAPER-Konto – Kauf-Limit, Stop beim Broker,
                          Ziel/48 h durch den Bot (Alpaca kann bei Krypto kein OCO und kein Short).
  Shorts (ETH, ADA):      Simulation mit Live-Kursen (keine Gratis-Demo mit Short von GitHub aus erreichbar).
  Alle 5 zusätzlich:      Backtest-identische Simulation als Vergleich (daten_p4/).

Gemeinsames Demo-Konto in Fr. (Start 100): Risiko 3 % pro Trade, Gewinne NACH OKX-Perpetual-Gebühren.
Telegram: nur Einstieg, Ausstieg, Wochenbericht (3 Zeilen), Not-Aus und Fehler.
"""
import json, os, sys, time, traceback
from datetime import datetime, timezone

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
from strategie import TAG, ch_uhr_utc  # noqa: E402

OKX_MAKER, OKX_TAKER = 0.0002, 0.0005   # OKX Perpetual (Einstieg Limit = Maker, Ausstieg = Taker)
LONGS = [  # Alpaca-Paper
    dict(name="BTC Long", coin="BTC", alpaca="BTC/USD", binance="BTCUSDT", sma=50, prefix="p4btcl"),
    dict(name="ETH Long", coin="ETH", alpaca="ETH/USD", binance="ETHUSDT", sma=50, prefix="p4ethl"),
    dict(name="SOL Long", coin="SOL", alpaca="SOL/USD", binance="SOLUSDT", sma=20, prefix="p4soll"),
]
SHORTS = ["ETH Short", "ADA Short"]
KOPF = "quelle;strategie;richtung;eroeffnet_utc;geschlossen_utc;einstieg;ausstieg;grund;r;gewinn_fr;konto_fr\n"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


class Konto:
    """Gemeinsames Demo-Konto (Fr.) + Journal aller ausgeführten Trades."""
    def __init__(self, ordner, start=100.0, rueckgang=40.0):
        os.makedirs(ordner, exist_ok=True)
        self.pfad, self.jpfad, self.rueckgang = os.path.join(ordner, "konto.json"), os.path.join(ordner, "journal.csv"), rueckgang
        try:
            with open(self.pfad, encoding="utf-8") as f:
                self.z = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.z = {"start": start, "konto": start, "hoch": start, "halt": False}

    def wert(self):
        return self.z["konto"]

    def buchen(self, x):
        self.z["konto"] += x["gewinn"]
        self.z["hoch"] = max(self.z["hoch"], self.z["konto"])
        neu = not os.path.exists(self.jpfad)
        with open(self.jpfad, "a", encoding="utf-8") as f:
            if neu:
                f.write(KOPF)
            f.write(f"{x['quelle']};{x['strategie']};{x['richtung']};{iso(x['eroeffnet'])};{iso(x['geschlossen'])};"
                    f"{x['einstieg']:.6g};{x['ausstieg']:.6g};{x['grund']};{x['r']:.3f};{x['gewinn']:.4f};{self.z['konto']:.4f}\n")
        if self.z["konto"] < self.z["hoch"] * (1 - self.rueckgang / 100):
            self.z["halt"] = True
        self.speichern()
        return self.z["konto"]

    def speichern(self):
        tmp = self.pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.z, f, indent=1)
        os.replace(tmp, self.pfad)


def wochenbericht(konto, melder, jetzt):
    d = datetime.fromtimestamp(jetzt, timezone.utc)
    woche = d.strftime("%G-W%V")
    if d.weekday() != 6 or jetzt < ch_uhr_utc(int(jetzt) // TAG * TAG, "19:00") or konto.z.get("bericht_woche") == woche:
        return
    konto.z["bericht_woche"] = woche
    seit = jetzt - 7 * TAG
    rows = []
    if os.path.exists(konto.jpfad):
        with open(konto.jpfad, encoding="utf-8") as f:
            for z in f.readlines()[1:]:
                s = z.strip().split(";")
                if len(s) <= 9:
                    continue
                try:  # eine unvollständige oder kaputte Zeile darf den Bericht nicht stoppen
                    geschlossen = datetime.strptime(s[4], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
                    gewinn = float(s[9])
                except ValueError:
                    continue
                if geschlossen >= seit:
                    rows.append(gewinn)
    gew = sum(1 for x in rows if x > 0)
    start = konto.z.get("start") or 100.0
    melder("📊 Wochenbericht (Demo)",
           f"Trades: {len(rows)} · Gewinner: {gew} · Verlierer: {len(rows) - gew}\n"
           f"Woche: {sum(rows):+.2f} Fr. (nach OKX-Gebühren)\n"
           f"Konto: {konto.wert():.2f} Fr. ({(konto.wert() / start - 1) * 100:+.1f} % seit Start)")
    konto.speichern()


def main():
    from daten import Daten
    from bot import Bot, lade_env, telegram_melder
    from p4_bot import P4Bot
    env = lade_env(os.environ.get("TRADELAB_ENV", os.path.expanduser("~/.tradelab.env")))
    basis = os.environ.get("TRADELAB_DEMO_DATEN", os.path.join(HIER, "daten_demo"))
    cfg_p4 = {}
    try:
        with open(os.path.join(HIER, "p4_einstellungen.json"), encoding="utf-8") as f:
            cfg_p4 = json.load(f)
    except FileNotFoundError:
        pass
    melder = telegram_melder(env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID"))
    konto = Konto(basis, cfg_p4.get("start_kapital", 100.0), cfg_p4.get("rueckgang_stopp_prozent", 40.0))
    halt_vorher = konto.z.get("halt")
    aktiv = cfg_p4.get("handel_aktiv", True) and not konto.z.get("halt")
    fehler = []
    cache = {}

    def daten_fuer(sym):
        if sym not in cache:
            cache[sym] = Daten(melden=lambda t: None, symbol=sym)
        return cache[sym]

    # 1) Simulation (alle 5 als Vergleich; Shorts zählen als ausgeführte Trades)
    try:
        sim = P4Bot(daten_fuer, melder, os.environ.get("TRADELAB_P4_DATEN", os.path.join(HIER, "daten_p4")),
                    dict(cfg_p4, handel_aktiv=True), ausfuehren=SHORTS if aktiv else [], konto_fn=konto.wert, buchung=konto.buchen)
        if not aktiv:  # Not-Aus: Shorts nicht mehr als Demo-Trades zählen (Sim läuft als Vergleich weiter)
            sim.ausfuehren = []
        sim.tick()
    except Exception as e:
        fehler.append(f"Simulation: {e}")
        print(traceback.format_exc(), flush=True)

    # 2) Longs bei Alpaca Paper
    if env.get("ALPACA_KEY_ID") and env.get("ALPACA_SECRET_KEY"):
        from alpaca import Alpaca
        url = (env.get("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").strip()
        if "paper-api." not in url:
            fehler.append("ALPACA_BASE_URL ist nicht Paper – Longs übersprungen")
        else:
            for s in LONGS:
                try:
                    d = daten_fuer(s["binance"])
                    br = Alpaca(env["ALPACA_KEY_ID"].strip(), env["ALPACA_SECRET_KEY"].strip(), url, daten=d,
                                symbol=s["alpaca"], maker=OKX_MAKER, taker=OKX_TAKER)
                    cfg = dict(name=s["name"], coin=s["coin"], id_prefix=s["prefix"], sma_tage=s["sma"],
                               risiko_prozent=cfg_p4.get("risiko_prozent", 3.0), rueckgang_stopp_prozent=99.0,
                               ende_utc_stunde=cfg_p4.get("ende_utc_stunde", 20), nur_trades=True, handel_aktiv=aktiv,
                               broker_name="Alpaca", strategie=f"P4 {s['name']} (Alpaca Paper)")
                    bot = Bot(br, d, melder, os.path.join(basis, s["prefix"]), cfg, konto_fn=konto.wert, buchung=konto.buchen)
                    br.z = bot.z.setdefault("broker", {})
                    try:
                        bot.tick()
                    finally:
                        bot.speichern()
                except Exception as e:
                    fehler.append(f"{s['name']}: {e}")
                    print(traceback.format_exc(), flush=True)
    else:
        fehler.append("Alpaca-Schlüssel fehlen – Longs laufen nur in der Simulation")

    # 3) Not-Aus, Wochenbericht, Fehler
    if konto.z.get("halt") and not halt_vorher:
        melder("🚨 NOT-AUS (Demo)", f"Konto {konto.wert():.2f} Fr. ist über {konto.rueckgang:.0f} % unter dem Höchststand "
                                   f"{konto.z['hoch']:.2f} Fr. Keine neuen Trades mehr. Offene Trades behalten ihren Stop.")
    try:  # der Wochenbericht darf den Lauf nie rot machen
        wochenbericht(konto, melder, time.time())
    except Exception as e:
        fehler.append(f"Wochenbericht: {e}")
        print(traceback.format_exc(), flush=True)
    if fehler:
        konto.z["fehler_in_folge"] = konto.z.get("fehler_in_folge", 0) + 1
        print("FEHLER: " + " | ".join(fehler), flush=True)
        if konto.z["fehler_in_folge"] in (3, 30, 300):
            melder("🚨 Bot-Fehler", f"{konto.z['fehler_in_folge']}x hintereinander:\n" + "\n".join(fehler)[:500])
    else:
        konto.z["fehler_in_folge"] = 0
    konto.speichern()


if __name__ == "__main__":
    main()
