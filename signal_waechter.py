# -*- coding: utf-8 -*-
"""
TradeLab V2 – Signal-Wächter (NUR Hinweise, KEINE Orders, kein Broker, keine API-Keys)

Prüft regelmässig die Watchlist mit exakt den Regeln des TradingView-Indikators
"TradeLab V2 Trendfolge" und meldet neue Signale per Windows-Benachrichtigung:
  1) Trendfilter : Tages-Schlusskurs > SMA 200 (Tagesbasis)
  2) Einstieg    : Schlusskurs > Hoch der vorherigen 20 Kerzen -> Kauf zur Eröffnung der nächsten Kerze
  3) Stop-Loss   : Einstieg - 2 x ATR(14)
  4) Ausstieg    : Schlusskurs < Tief der vorherigen 10 Kerzen
  5) Risiko      : 0,5 % vom Konto
Krypto = 4h-Kerzen (Binance), Aktien = Tageskerzen (Yahoo). Nur abgeschlossene Kerzen.

Start:  waechter_starten.bat  (Fenster offen lassen)
Test :  waechter_test.bat     (einmal prüfen + Test-Benachrichtigung)
Nur Python-Standardbibliothek – nichts muss installiert werden.
"""
import json, os, sys, time, subprocess, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
CFG_DATEI = os.path.join(HIER, "einstellungen.json")
STATE_DATEI = os.path.join(HIER, "zustand.json")
LOG_DATEI = os.path.join(HIER, "signale_log.txt")

STANDARD_CFG = {
    "kontostand_usd": 100000,
    "risiko_prozent": 0.5,
    "pruefen_alle_minuten": 10,
    "krypto_4h": ["BTCUSDT", "ETHUSDT"],
    "aktien_1d": ["AAPL", "MSFT", "AMZN", "SPY"],
    "einstieg_kerzen": 20,
    "ausstieg_kerzen": 10,
    "sma_tage": 200,
    "atr_laenge": 14,
    "stop_atr": 2.0,
    "telegram_token": "",
    "telegram_chat_id": "",
}
UA = {"User-Agent": "Mozilla/5.0 (TradeLab-Signal-Waechter)"}


# ---------------------------------------------------------------- Hilfen
def jetzt():
    return datetime.now(timezone.utc)


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


TELEGRAM = {"token": "", "chat_id": ""}


def telegram_einrichten(cfg):
    """Liest Token aus einstellungen.json. Fehlt die Chat-ID, wird sie automatisch
    aus der letzten Nachricht an den Bot geholt und gespeichert."""
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
            log("Hinweis: Telegram-Chat-ID fehlt. Schick deinem Bot zuerst eine Nachricht und starte neu.")
            return
        cfg["telegram_chat_id"] = chat_id
        speichere_json(CFG_DATEI, cfg)
        log("Telegram-Chat-ID gefunden und gespeichert.")
    TELEGRAM["token"], TELEGRAM["chat_id"] = token, chat_id


def telegram_senden(titel, text):
    if not (TELEGRAM["token"] and TELEGRAM["chat_id"]):
        return
    try:
        daten = urllib.parse.urlencode({"chat_id": TELEGRAM["chat_id"], "text": f"{titel}\n{text}"}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM['token']}/sendMessage", data=daten, headers=UA)
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # Telegram darf den Wächter nie stoppen
        log(f"Hinweis: Telegram-Nachricht fehlgeschlagen ({e})")


def benachrichtigen(titel, text):
    """Windows-Benachrichtigung (Sprechblase unten rechts) + Ton + Telegram (falls eingerichtet)."""
    telegram_senden(titel, text)
    if os.name != "nt":
        print(f"[BENACHRICHTIGUNG] {titel}: {text}")
        return
    t = titel.replace("'", "’"); m = text.replace("'", "’")
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$n.BalloonTipTitle = '{t}'; $n.BalloonTipText = '{m}'; $n.Visible = $true; "
        "$n.ShowBalloonTip(15000); [System.Media.SystemSounds]::Exclamation.Play(); Start-Sleep -Seconds 16; $n.Dispose()"
    )
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # Benachrichtigung darf den Wächter nie stoppen
        log(f"Hinweis: Benachrichtigung fehlgeschlagen ({e})")


# ---------------------------------------------------------------- Daten
def binance(symbol, intervall, limit=1000):
    """Nur abgeschlossene Kerzen. Hauptquelle api.binance.com, Ersatz data-api.binance.vision."""
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
    kerzen = []
    for k in roh:
        if int(k[6]) >= jetzt_ms:  # noch nicht abgeschlossen
            continue
        kerzen.append(dict(t=int(k[0]) // 1000, o=float(k[1]), h=float(k[2]), l=float(k[3]), c=float(k[4])))
    return kerzen


def yahoo_tag(symbol):
    """Tageskerzen (dividenden-/split-bereinigt). Die laufende Handelstag-Kerze wird weggelassen."""
    j = None
    for host in ("query1", "query2"):
        try:
            j = hole(f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d&events=div,split")
            break
        except Exception as e:
            fehler = e
    if j is None:
        raise RuntimeError(f"Yahoo nicht erreichbar: {fehler}")
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    ende = r["meta"].get("currentTradingPeriod", {}).get("regular", {}).get("end")
    start = r["meta"].get("currentTradingPeriod", {}).get("regular", {}).get("start")
    kerzen = []
    for i, ts in enumerate(r["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        f = (adj[i] / c) if adj and adj[i] else 1.0
        kerzen.append(dict(t=int(ts), o=o * f, h=h * f, l=l * f, c=c * f))
    # Heutige Kerze nur behalten, wenn der Handelstag vorbei ist (+15 Min. Puffer)
    if kerzen and start and ende and kerzen[-1]["t"] >= start and time.time() < ende + 900:
        kerzen.pop()
    return kerzen


# ---------------------------------------------------------------- Indikatoren
def atr_wilder(k, n):
    out, prev = [], None
    for i, x in enumerate(k):
        tr = x["h"] - x["l"] if i == 0 else max(x["h"] - x["l"], abs(x["h"] - k[i - 1]["c"]), abs(x["l"] - k[i - 1]["c"]))
        prev = tr if prev is None else prev + (tr - prev) / n
        out.append(prev if i >= n - 1 else None)
    return out


def sma(werte, n):
    out, s = [], 0.0
    for i, v in enumerate(werte):
        s += v
        if i >= n:
            s -= werte[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def trendfilter_tag(tageskerzen, n):
    """Datum (UTC) -> True/False: Tages-Schluss > SMA(n) an diesem Tag."""
    s = sma([x["c"] for x in tageskerzen], n)
    d = {}
    for x, m in zip(tageskerzen, s):
        tag = datetime.fromtimestamp(x["t"], timezone.utc).date()
        d[tag] = (m is not None and x["c"] > m, x["c"], m)
    return d


def auswerten(k, trend, cfg):
    """Gleiche Logik wie der Pine-Indikator. trend[i] = (ok, tagesschluss, sma)."""
    nIn, nOut, nAtr, mul = cfg["einstieg_kerzen"], cfg["ausstieg_kerzen"], cfg["atr_laenge"], cfg["stop_atr"]
    a = atr_wilder(k, nAtr)
    n = len(k)
    inPos, entry, stop = False, None, None
    ereignisse = [None] * n
    rawPrev = False
    for i in range(n):
        up = max(x["h"] for x in k[i - nIn:i]) if i >= nIn else None
        ex = min(x["l"] for x in k[i - nOut:i]) if i >= nOut else None
        raw = up is not None and a[i] is not None and trend[i][0] and k[i]["c"] > up
        ev = dict(up=up, ex=ex, atr=a[i], raw=raw, kauf=False, stop_hit=False, raus=False, neu=False)
        if not inPos and rawPrev and a[i - 1] is not None:
            inPos, entry, stop = True, k[i]["o"], k[i]["o"] - mul * a[i - 1]
            ev["neu"] = True
        if inPos:
            if k[i]["l"] <= stop:
                ev["stop_hit"] = True
            elif ex is not None and k[i]["c"] < ex:
                ev["raus"] = True
        ev["kauf"] = raw and not inPos
        ev["inPos"], ev["entry"], ev["stop"] = inPos, entry, stop
        if ev["stop_hit"] or ev["raus"]:
            inPos, entry, stop = False, None, None
        ereignisse[i] = ev
        rawPrev = raw
    return ereignisse


# ---------------------------------------------------------------- Markt prüfen
def fmt(x, nachkomma=2):
    return "-" if x is None else f"{x:,.{nachkomma}f}".replace(",", "'")


def pruefe_markt(symbol, art, cfg, zustand, test=False):
    if art == "krypto":
        k = binance(symbol, "4h", 1000)
        tage = binance(symbol, "1d", 400)
        tf = trendfilter_tag(tage, cfg["sma_tage"])
        trend = []
        for x in k:  # Schluss des LETZTEN abgeschlossenen Tages (wie Indikator/Backtest)
            gestern = datetime.fromtimestamp(x["t"], timezone.utc).date() - timedelta(days=1)
            trend.append(tf.get(gestern, (False, None, None)))
        name = symbol.replace("USDT", "") + " (4h)"
    else:
        k = yahoo_tag(symbol)
        s = sma([x["c"] for x in k], cfg["sma_tage"])
        trend = [(m is not None and x["c"] > m, x["c"], m) for x, m in zip(k, s)]
        name = symbol + " (1D)"
    if len(k) < cfg["sma_tage"] // 6:
        raise RuntimeError("zu wenig Kursdaten")

    ev = auswerten(k, trend, cfg)
    letzte, e = k[-1], ev[-1]
    kerze_zeit = datetime.fromtimestamp(letzte["t"], timezone.utc)
    schluessel = f"{symbol}"
    bereits = zustand.get(schluessel, {}).get("letzte_kerze")

    risiko = cfg["kontostand_usd"] * cfg["risiko_prozent"] / 100
    stopabstand = cfg["stop_atr"] * e["atr"] if e["atr"] else None
    menge = risiko / stopabstand if stopabstand else None
    ok, tclose, tsma = trend[-1]
    abstand = (e["up"] / letzte["c"] - 1) * 100 if e["up"] else None

    if e["kauf"]:
        status = "KAUF-SIGNAL"
    elif e["stop_hit"]:
        status = "STOP getroffen"
    elif e["raus"]:
        status = "AUSSTIEG (10er-Tief)"
    elif e["inPos"]:
        status = "Position offen"
    elif not ok:
        status = "Kein Trade (unter SMA 200)"
    elif abstand is not None and abstand <= 3:
        status = "Setup in Vorbereitung"
    else:
        status = "Kein Signal"

    zeile = (f"{name:<10} Schluss {fmt(letzte['c'])} | {status:<27} | Ausbruch über {fmt(e['up'])} "
             f"({fmt(abstand, 1)} %) | Ausstieg {fmt(e['ex'])} | SMA200 {fmt(tsma)} | Kerze {kerze_zeit:%d.%m. %H:%M} UTC")

    # Verpasste Kerzen (Laptop war aus): Signale der letzten Kerzen seit der letzten Prüfung nachmelden
    if bereits is not None and not test:
        for x, ev_alt in list(zip(k, ev))[-30:-1]:
            if x["t"] <= bereits:
                continue
            zeit = datetime.fromtimestamp(x["t"], timezone.utc).strftime("%d.%m. %H:%M")
            if ev_alt["kauf"]:
                benachrichtigen(f"VERPASST: KAUF {name}", f"KAUF-Signal am {zeit} UTC (Laptop war aus). Im Chart prüfen.")
                log(f"*** VERPASST: KAUF-SIGNAL {name} am {zeit} UTC, Schluss {fmt(x['c'])}")
            elif ev_alt["stop_hit"]:
                benachrichtigen(f"VERPASST: STOP {name}", f"Stop {fmt(ev_alt['stop'])} am {zeit} UTC getroffen.")
                log(f"*** VERPASST: STOP {name} am {zeit} UTC")
            elif ev_alt["raus"]:
                benachrichtigen(f"VERPASST: AUSSTIEG {name}", f"Ausstieg am {zeit} UTC (Schluss unter 10er-Tief).")
                log(f"*** VERPASST: AUSSTIEG {name} am {zeit} UTC")

    neu = bereits != letzte["t"]
    if neu or test:
        if e["kauf"]:
            st = letzte["c"] - stopabstand
            benachrichtigen(f"KAUF-Signal {name}",
                            f"Schluss {fmt(letzte['c'])} über 20er-Hoch {fmt(e['up'])}. Kauf zur nächsten Eröffnung. "
                            f"Stop ca. {fmt(st)} (2 ATR = {fmt(stopabstand)}). Menge ca. {fmt(menge, 4)} für {fmt(risiko, 0)} USD Risiko.")
            log(f"*** KAUF-SIGNAL {name}: Schluss {fmt(letzte['c'])}, Stop ca. {fmt(st)}, Menge ca. {fmt(menge, 4)}")
        elif e["stop_hit"]:
            benachrichtigen(f"STOP {name}", f"Stop {fmt(e['stop'])} wurde getroffen. Ins Journal eintragen.")
            log(f"*** STOP {name}: Stop {fmt(e['stop'])} getroffen")
        elif e["raus"]:
            benachrichtigen(f"AUSSTIEG {name}", f"Schluss {fmt(letzte['c'])} unter 10er-Tief {fmt(e['ex'])}. Position schliessen.")
            log(f"*** AUSSTIEG {name}: Schluss {fmt(letzte['c'])} unter {fmt(e['ex'])}")
        zustand[schluessel] = {"letzte_kerze": letzte["t"], "status": status}
    return zeile


def runde(cfg, zustand, test=False, speichern=True):
    zeilen = []
    for s in cfg["krypto_4h"]:
        try:
            zeilen.append(pruefe_markt(s, "krypto", cfg, zustand, test))
        except Exception as e:
            zeilen.append(f"{s:<10} FEHLER: {e}")
    for s in cfg["aktien_1d"]:
        try:
            zeilen.append(pruefe_markt(s, "aktie", cfg, zustand, test))
        except Exception as e:
            zeilen.append(f"{s:<10} FEHLER: {e}")
    if speichern:
        speichere_json(STATE_DATEI, zustand)
    return zeilen


def main():
    cfg = dict(STANDARD_CFG)
    cfg.update(lade_json(CFG_DATEI, {}))
    if not os.path.exists(CFG_DATEI):
        speichere_json(CFG_DATEI, cfg)
    zustand = lade_json(STATE_DATEI, {})
    test = "--test" in sys.argv
    telegram_einrichten(cfg)

    print("=" * 100)
    print(" TradeLab V2 – Signal-Wächter · NUR Hinweise, keine Orders · Fenster offen lassen")
    print(f" Konto {fmt(cfg['kontostand_usd'], 0)} USD · Risiko {cfg['risiko_prozent']} % · prüft alle {cfg['pruefen_alle_minuten']} Min.")
    print(f" Telegram: {'aktiv' if TELEGRAM['chat_id'] else 'aus (Token/Chat-ID in einstellungen.json fehlt)'}")
    print("=" * 100)

    if test:
        benachrichtigen("TradeLab V2 Test", "Der Signal-Wächter funktioniert. So sieht ein Signal aus.")
        for z in runde(cfg, {}, test=False, speichern=False):
            print(" " + z)
        log("Testlauf beendet.")
        return

    if "--einmal" in sys.argv:  # Cloud-Modus (GitHub Actions): eine Prüfung, dann Ende
        print(f"\n--- Prüfung {datetime.now():%d.%m.%Y %H:%M} ---")
        for z in runde(cfg, zustand):
            print(" " + z)
        return

    log("Wächter gestartet.")
    while True:
        try:
            print(f"\n--- Prüfung {datetime.now():%d.%m.%Y %H:%M} ---")
            for z in runde(cfg, zustand):
                print(" " + z)
        except Exception as e:
            log(f"FEHLER (Wächter läuft weiter): {e}")
        time.sleep(max(1, int(cfg["pruefen_alle_minuten"])) * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Wächter beendet.")
