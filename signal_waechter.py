# -*- coding: utf-8 -*-
"""
TradeLab V3 – Signal-Wächter "Rücksetzer nur Long, Ziel 2R" (NUR Hinweise, KEINE Orders)

Strategie (Backtest BTC 2021–2026, Limit-Einstieg, nach Gebühren):
  1) Trendfilter : Tages-Schlusskurs von gestern > SMA 20 (Tage). Sonst heute kein Trade.
  2) Einstieg    : Kauf-Limit = Tageseröffnung (00:00 UTC) - 0,3 x ATR(14 Tage). Gültig bis Tagesende UTC.
  3) Stop-Loss   : Einstieg - 0,8 x ATR
  4) Ziel        : Einstieg + 2 x Stopabstand (2R)
  5) Zeit-Ausstieg: nach 48 Stunden zum Marktpreis schliessen
  6) Risiko      : risiko_prozent vom Konto pro Trade
Markt: BTCUSDT (Binance). Max. 1 Trade pro Tag.

Jeden Tag kurz nach 01:00 Uhr (CH) kommt eine Tagesnachricht:
  - entweder die fertige Limit-Order (Kauf, Stop, Ziel, Menge)
  - oder "heute kein Trade" (Trend nach unten)

Start lokal : python signal_waechter.py          (prüft alle X Minuten)
Cloud       : python signal_waechter.py --einmal (GitHub Actions, jede Stunde)
Test        : python signal_waechter.py --test   (zeigt den heutigen Plan, ändert nichts)
Nur Python-Standardbibliothek.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
CFG_DATEI = os.path.join(HIER, "einstellungen.json")
STATE_DATEI = os.path.join(HIER, "zustand.json")
LOG_DATEI = os.path.join(HIER, "signale_log.txt")
JOURNAL_DATEI = os.path.join(HIER, "journal.csv")
JOURNAL_KOPF = "eroeffnet_utc;geschlossen_utc;markt;einstieg;stop;ziel;ausstieg;grund;menge;gewinn_usd;r;konto_usd\n"

STANDARD_CFG = {
    "strategie": "V3 Rücksetzer Long 2R",
    "symbol": "BTCUSDT",
    "kontostand_usd": 100000,
    "risiko_prozent": 0.25,
    "pruefen_alle_minuten": 10,
    "sma_tage": 20,
    "atr_tage": 14,
    "ruecksetzer_atr": 0.3,
    "stop_atr": 0.8,
    "ziel_r": 2.0,
    "max_stunden": 48,
    "telegram_token": "",
    "telegram_chat_id": "",
}
UA = {"User-Agent": "Mozilla/5.0 (TradeLab-Signal-Waechter)"}
TAG = 86400


# ---------------------------------------------------------------- Hilfen
def jetzt():
    return datetime.now(timezone.utc)


def zeit(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m.%Y %H:%M")


def zeit_ch(ts):
    """Schweizer Zeit (MEZ/MESZ, einfache Regel: letzter Sonntag März/Oktober)."""
    d = datetime.fromtimestamp(ts, timezone.utc)
    def letzter_sonntag(jahr, monat):
        x = datetime(jahr, monat + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
        return x - timedelta(days=(x.weekday() + 1) % 7)
    sommer = letzter_sonntag(d.year, 3).replace(hour=1) <= d < letzter_sonntag(d.year, 10).replace(hour=1)
    return (d + timedelta(hours=2 if sommer else 1)).strftime("%d.%m. %H:%M")


def log(text):
    zeile = f"{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}  {text}"
    print(zeile, flush=True)
    with open(LOG_DATEI, "a", encoding="utf-8") as f:
        f.write(zeile + "\n")


def lade_json(pfad, standard):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return standard


def speichere_json(pfad, daten):
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f, indent=2, ensure_ascii=False)
    os.replace(tmp, pfad)


def hole(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fmt(x, nachkomma=2):
    return "-" if x is None else f"{x:,.{nachkomma}f}".replace(",", "'")


# ---------------------------------------------------------------- Telegram
TELEGRAM = {"token": "", "chat_id": ""}


def telegram_einrichten(cfg):
    token = (os.environ.get("TELEGRAM_TOKEN") or str(cfg.get("telegram_token", ""))).strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or str(cfg.get("telegram_chat_id", ""))).strip()
    if not token:
        return
    if not chat_id:
        try:
            j = hole(f"https://api.telegram.org/bot{token}/getUpdates")
            for u in reversed(j.get("result", [])):
                msg = u.get("message") or u.get("edited_message") or {}
                if msg.get("chat", {}).get("id"):
                    chat_id = str(msg["chat"]["id"])
                    break
        except Exception as e:
            log(f"Hinweis: Telegram nicht erreichbar ({e})")
            return
        if not chat_id:
            log("Hinweis: Telegram-Chat-ID fehlt. Schick deinem Bot zuerst eine Nachricht.")
            return
        if not os.environ.get("TELEGRAM_TOKEN"):
            cfg["telegram_chat_id"] = chat_id
            speichere_json(CFG_DATEI, cfg)
    TELEGRAM["token"], TELEGRAM["chat_id"] = token, chat_id


def telegram_senden(titel, text):
    print(f"[TELEGRAM] {titel}\n{text}\n", flush=True)
    if not (TELEGRAM["token"] and TELEGRAM["chat_id"]):
        return
    try:
        daten = urllib.parse.urlencode({"chat_id": TELEGRAM["chat_id"], "text": f"{titel}\n{text}"}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM['token']}/sendMessage", data=daten, headers=UA)
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # Telegram darf den Wächter nie stoppen
        log(f"Hinweis: Telegram-Nachricht fehlgeschlagen ({e})")


# ---------------------------------------------------------------- Daten (Binance)
def binance(symbol, intervall, limit=1000, nur_fertig=True):
    fehler = None
    for basis in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            roh = hole(f"{basis}/api/v3/klines?symbol={symbol}&interval={intervall}&limit={limit}")
            break
        except Exception as e:
            fehler = e
    else:
        raise RuntimeError(f"Binance nicht erreichbar: {fehler}")
    jetzt_ms = int(time.time() * 1000)
    out = []
    for k in roh:
        fertig = int(k[6]) < jetzt_ms
        if nur_fertig and not fertig:
            continue
        out.append(dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4]), fertig=fertig))
    return out


# ---------------------------------------------------------------- Tagesplan
def tagesplan(cfg, tage):
    """tage = Tageskerzen inkl. heutiger (laufender) Kerze. Plan nur aus Daten bis gestern + heutiger Eröffnung."""
    heute = tage[-1]
    alt = [x for x in tage[:-1] if x["fertig"]]
    n_sma, n_atr = cfg["sma_tage"], cfg["atr_tage"]
    if len(alt) < max(n_sma, n_atr) + 1:
        raise RuntimeError("zu wenig Tageskerzen")
    schluss = alt[-1]["c"]
    sma = sum(x["c"] for x in alt[-n_sma:]) / n_sma
    trs = []
    for i in range(len(alt) - n_atr, len(alt)):
        x, v = alt[i], alt[i - 1]
        trs.append(max(x["h"] - x["l"], abs(x["h"] - v["c"]), abs(x["l"] - v["c"])))
    atr = sum(trs) / n_atr
    plan = dict(tag=heute["t"], eroeffnung=heute["o"], gestern_schluss=schluss, sma=sma, atr=atr,
                trend=schluss > sma, status="wartet")
    if plan["trend"]:
        limit = heute["o"] - cfg["ruecksetzer_atr"] * atr
        abstand = cfg["stop_atr"] * atr
        plan.update(limit=limit, stop=limit - abstand, ziel=limit + cfg["ziel_r"] * abstand, abstand=abstand)
    else:
        plan["status"] = "kein_trade"
    return plan


def tagesnachricht(plan, cfg, konto, offen):
    name = cfg["symbol"].replace("USDT", "")
    ende = zeit_ch(plan["tag"] + TAG)
    if not plan["trend"]:
        text = (f"Heute KEIN Trade: Trend nach unten.\n"
                f"Gestern Schluss {fmt(plan['gestern_schluss'])} liegt unter SMA {cfg['sma_tage']} ({fmt(plan['sma'])}).")
    else:
        risiko = konto * cfg["risiko_prozent"] / 100
        menge = risiko / plan["abstand"]
        text = (f"LIMIT-KAUF {name} heute:\n"
                f"• Kauf-Limit: {fmt(plan['limit'])}\n"
                f"• Stop-Loss: {fmt(plan['stop'])}  (−{fmt(plan['abstand'] / plan['limit'] * 100)} %)\n"
                f"• Ziel (2R): {fmt(plan['ziel'])}\n"
                f"• Menge: {fmt(menge, 4)} {name}  (Risiko {fmt(risiko, 0)} USD)\n"
                f"• Limit gültig bis {ende} Uhr. Wird es nicht erreicht: Order löschen.\n"
                f"• Nach 48 h ohne Stop/Ziel: zum Marktpreis verkaufen.\n"
                f"Eröffnung {fmt(plan['eroeffnung'])} · ATR {fmt(plan['atr'])}")
    text += f"\nKonto {fmt(konto, 0)} USD · offene Trades: {len(offen)}"
    telegram_senden(f"📅 Tagesplan {datetime.fromtimestamp(plan['tag'], timezone.utc):%d.%m.%Y} · {cfg['strategie']}", text)


# ---------------------------------------------------------------- Journal
def journal_zustand(zustand, cfg):
    j = zustand.setdefault("_journal", {})
    j.setdefault("konto", float(cfg["kontostand_usd"]))
    j.setdefault("offen", {})
    return j


def trade_schliessen(j, tid, preis, grund, ts, cfg):
    t = j["offen"].pop(tid)
    gewinn = (preis - t["einstieg"]) * t["menge"]
    gebuehr = 0.001 * t["einstieg"] * t["menge"]  # ca. 0,1 % Gebühren/Slippage (wie Backtest)
    gewinn -= gebuehr
    r = gewinn / t["risiko"] if t["risiko"] else 0
    j["konto"] += gewinn
    neu = not os.path.exists(JOURNAL_DATEI)
    with open(JOURNAL_DATEI, "a", encoding="utf-8") as f:
        if neu:
            f.write(JOURNAL_KOPF)
        f.write(f"{zeit(t['ts'])};{zeit(ts)};{cfg['symbol']};{t['einstieg']:.2f};{t['stop']:.2f};{t['ziel']:.2f};"
                f"{preis:.2f};{grund};{t['menge']:.6f};{gewinn:.2f};{r:.2f};{j['konto']:.2f}\n")
    zeichen = "✅" if gewinn >= 0 else "❌"
    aktion = {"Ziel": "Ziel erreicht – Position ist zu (Take-Profit).",
              "Stop": "Stop getroffen – Position ist zu.",
              "48h": "48 Stunden vorbei – JETZT zum Marktpreis verkaufen!"}[grund]
    telegram_senden(f"{zeichen} {cfg['symbol'].replace('USDT', '')}: {grund}",
                    f"{aktion}\nEinstieg {fmt(t['einstieg'])} → Ausstieg {fmt(preis)}\n"
                    f"Ergebnis {gewinn:+,.0f} USD ({r:+.2f} R) · Konto {fmt(j['konto'], 0)} USD".replace(",", "'"))
    log(f"JOURNAL: {grund} {gewinn:+.2f} USD ({r:+.2f} R)")


def kerzen_verarbeiten(kerzen, zustand, cfg):
    """Spielt neue 5-Min-Kerzen nach: Limit ausgelöst? Stop? Ziel? 48 h?"""
    j = journal_zustand(zustand, cfg)
    plaene = zustand.setdefault("_plaene", {})
    letzte = zustand.get("_letzte_5m", 0)
    for x in kerzen:
        if x["t"] <= letzte:
            continue
        # 1) offene Trades prüfen (nicht auf der Einstiegskerze -> wie Backtest; Stop zuerst = vorsichtig)
        for tid in list(j["offen"]):
            t = j["offen"][tid]
            if x["t"] < t["ts"]:
                continue
            if x["l"] <= t["stop"]:
                trade_schliessen(j, tid, min(t["stop"], x["o"]), "Stop", x["t"], cfg)
            elif x["t"] > t["ts"] and x["h"] >= t["ziel"]:
                trade_schliessen(j, tid, t["ziel"], "Ziel", x["t"], cfg)
            elif x["t"] + 300 >= t["ts"] + cfg["max_stunden"] * 3600:
                trade_schliessen(j, tid, x["c"], "48h", x["t"], cfg)
        # 2) Limit des Tages ausgelöst?
        tag = str(x["t"] - x["t"] % TAG)
        p = plaene.get(tag)
        if p and p["status"] == "wartet" and x["l"] <= p["limit"]:
            einstieg = min(p["limit"], x["o"])
            risiko = j["konto"] * cfg["risiko_prozent"] / 100
            menge = risiko / p["abstand"]
            stop, ziel = einstieg - p["abstand"], einstieg + cfg["ziel_r"] * p["abstand"]
            j["offen"][tag] = dict(ts=x["t"], einstieg=einstieg, stop=stop, ziel=ziel, menge=menge, risiko=risiko)
            p["status"] = "gekauft"
            telegram_senden(f"🟢 GEKAUFT {cfg['symbol'].replace('USDT', '')} (Limit ausgelöst)",
                            f"Einstieg {fmt(einstieg)} um {zeit_ch(x['t'])} Uhr\nStop {fmt(stop)} · Ziel {fmt(ziel)}\n"
                            f"Menge {fmt(menge, 4)} · Risiko {fmt(risiko, 0)} USD\n"
                            f"Spätester Verkauf: {zeit_ch(x['t'] + cfg['max_stunden'] * 3600)} Uhr")
            log(f"JOURNAL: gekauft zu {fmt(einstieg)}, Stop {fmt(stop)}, Ziel {fmt(ziel)}")
            # Stop auf derselben Kerze (vorsichtig, wie Backtest)
            if x["l"] <= stop:
                trade_schliessen(j, tag, stop, "Stop", x["t"], cfg)
        letzte = x["t"]
    zustand["_letzte_5m"] = letzte
    # alte, nicht ausgelöste Pläne verfallen lassen; nur 10 Tage aufbewahren
    heute = int(time.time()) // TAG * TAG
    for tag in list(plaene):
        if int(tag) < heute and plaene[tag]["status"] == "wartet":
            plaene[tag]["status"] = "verfallen"
        if int(tag) < heute - 10 * TAG:
            del plaene[tag]


def wochenbericht(zustand, cfg):
    jetzt_utc = jetzt()
    woche = jetzt_utc.strftime("%G-W%V")
    j = journal_zustand(zustand, cfg)
    if jetzt_utc.weekday() != 6 or jetzt_utc.hour < 18 or j.get("bericht_woche") == woche:
        return
    trades = []
    if os.path.exists(JOURNAL_DATEI):
        with open(JOURNAL_DATEI, encoding="utf-8") as f:
            trades = [z.strip().split(";") for z in f.readlines()[1:] if z.strip()]
    v3 = [t for t in trades if len(t) == 12]  # nur Trades der neuen Strategie
    g = [float(t[9]) for t in v3]
    rs = [float(t[10]) for t in v3]
    gew, verl = sum(x for x in g if x > 0), -sum(x for x in g if x <= 0)
    pf = f"{gew / verl:.2f}" if verl else "-"
    quote = f"{sum(x > 0 for x in g) / len(g) * 100:.0f} %" if g else "-"
    text = (f"Trades: {len(g)} · Treffer: {quote} · Profit-Faktor: {pf}\n"
            f"Summe: {sum(rs):+.1f} R · {sum(g):+,.0f} USD\n"
            f"Konto: {fmt(j['konto'], 0)} USD · offen: {len(j['offen'])}\n"
            f"Backtest-Erwartung: ca. 50 % Treffer, Profit-Faktor ca. 1,2").replace(",", "'")
    telegram_senden(f"📊 Wochenbericht · {cfg['strategie']}", text)
    j["bericht_woche"] = woche


# ---------------------------------------------------------------- Runde
def runde(cfg, zustand, test=False):
    sym = cfg["symbol"]
    tage = binance(sym, "1d", 60, nur_fertig=False)
    plan = tagesplan(cfg, tage)
    j = journal_zustand(zustand, cfg)
    if test:
        tagesnachricht(plan, cfg, j["konto"], j["offen"])
        return
    plaene = zustand.setdefault("_plaene", {})
    if "_letzte_5m" not in zustand:  # erster Start: nur ab jetzt zählen
        zustand["_letzte_5m"] = int(time.time()) // 300 * 300 - 300
    tag = str(plan["tag"])
    if tag not in plaene:
        plaene[tag] = plan
        log(f"Tagesplan {zeit(plan['tag'])}: " + (f"Limit {fmt(plan['limit'])}, Stop {fmt(plan['stop'])}, Ziel {fmt(plan['ziel'])}"
                                                  if plan["trend"] else "kein Trade (unter SMA)"))
        tagesnachricht(plan, cfg, j["konto"], j["offen"])
    kerzen = binance(sym, "5m", 1000)
    kerzen_verarbeiten(kerzen, zustand, cfg)
    wochenbericht(zustand, cfg)
    p = plaene[tag]
    print(f" {sym}: Plan heute = {p['status']}" + (f" · Limit {fmt(p.get('limit'))}" if p.get("limit") else "")
          + f" · offen {len(j['offen'])} · Konto {fmt(j['konto'], 0)} USD", flush=True)


def main():
    cfg = dict(STANDARD_CFG)
    cfg.update(lade_json(CFG_DATEI, {}))
    zustand = lade_json(STATE_DATEI, {})
    if zustand.get("_strategie") != cfg["strategie"]:  # Wechsel von V2: alten Zustand sichern, neu starten
        if zustand:
            speichere_json(os.path.join(HIER, "zustand_v2_backup.json"), zustand)
        if os.path.exists(JOURNAL_DATEI):
            os.replace(JOURNAL_DATEI, os.path.join(HIER, "journal_v2.csv"))
        zustand = {"_strategie": cfg["strategie"]}
    telegram_einrichten(cfg)
    if "--test" in sys.argv:
        runde(cfg, dict(zustand), test=True)
        return
    if "--einmal" in sys.argv:
        runde(cfg, zustand)
        speichere_json(STATE_DATEI, zustand)
        return
    log(f"Wächter gestartet ({cfg['strategie']}).")
    while True:
        try:
            runde(cfg, zustand)
            speichere_json(STATE_DATEI, zustand)
        except Exception as e:
            log(f"FEHLER (Wächter läuft weiter): {e}")
        time.sleep(max(1, int(cfg["pruefen_alle_minuten"])) * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Wächter beendet.")
