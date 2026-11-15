# TradeLab V3 Auto-Bot (OKX)

Vollautomatischer Bot für die Strategie **„Rücksetzer nur Long, Ziel 2R“** auf **OKX Spot BTC-USDT**.
Er läuft zuerst nur mit Spielgeld (OKX Demo Trading). Er braucht keine KI-Tokens, es ist ein normales Python-Programm (nur Standardbibliothek).

## Was der Bot macht
1. **00:00 UTC:** berechnet den Tagesplan aus den Binance-Tageskerzen (Trend über SMA 20, ATR 14).
2. **07:00 CH:** schickt den 📅 Tagesplan per Telegram.
3. **07:45 CH:** setzt bei OKX das Kauf-Limit (Tageseröffnung − 0,3 × ATR). Lag der Preis schon vorher darunter, gibt es keinen Trade.
4. **Gekauft:** setzt sofort eine **OCO-Order** bei OKX. Darin sind Stop (−0,8 × ATR) und Ziel (2R) zusammen, beide lösen als Market aus.
5. **Nach 48 h:** löscht die OCO und verkauft zum Marktpreis.
6. **Schutz:**
   - Not-Aus (`handel_aktiv`)
   - Stopp nach 3 % Rückgang vom Höchststand
   - Fehlt die OCO, wird sie neu gesetzt
   - Bei Neustart gibt es keine doppelten Orders
7. **Telegram:** Tagesplan, Order, Kauf, Ziel/Stop/48h, Fehler, sonntags Wochenbericht.

## Dateien
| Datei | Zweck |
|---|---|
| `bot.py` | Bot-Kern und Start |
| `strategie.py` | Rechenregeln (Plan, Menge, Zeitzonen) |
| `okx.py` | OKX-API v5 (Demo per Header `x-simulated-trading: 1`) |
| `daten.py` | Kurse von Binance, Ersatz OKX |
| `einstellungen.json` | Modus, Risiko, Not-Aus |
| `env.beispiel` | Vorlage für die geheime Datei `~/.tradelab.env` auf dem Server |
| `tradelab-bot.service` | systemd-Dienst (24/7, Neustart bei Absturz) |
| `install.sh` / `update.sh` | Einrichten / neuen Code einspielen |
| `tests/` | Unit-Tests, Replay (5 Jahre), Neustart-Test |

## Tests (Stand 15.11.2026)
- **Unit-Tests:** Zeitzonen (Sommer und Winter), Rundung, Plan, Menge und Höchstbetrag ✅
- **Replay** (BTCUSDT 5-Min, Dez 2021 – Okt 2026, simulierter OKX-Broker mit OCO, 0,08 % / 0,10 % Gebühren):
  - 298 Trades
  - bis Juni 2024: 52 % Treffer, Profit-Faktor 1,24
  - ab Juli 2024: 53 % Treffer, Profit-Faktor 1,28
  - Startkonto 5'000 → Ende 5'377 USDT, kein Not-Aus ausgelöst ✅
- **Neustart-Test:** 1 Jahr, Bot alle 97 Durchläufe neu gestartet. Gleiches Ergebnis, keine doppelten Orders ✅
- **Trockenlauf** mit echten Live-Daten ✅
- **Noch offen:** Test mit dem echten OKX-Demo-Konto (`python3 bot.py --test-order`), sobald Server und Schlüssel bereit sind.

## Unterschiede Backtest ↔ Live
- **Preise:** Signale von Binance, ausgeführt wird auf OKX. Die Preise können sich leicht unterscheiden.
- **Slippage:** Stop und Ziel lösen als Market aus. In schnellen Märkten kann der Ausführungspreis etwas schlechter sein.
- **Gebühren:** OKX-Gebühren können sich ändern. Ab ca. 0,3 % pro Trade verschwindet der Vorteil.
- **Teilfüllungen:** Teilweise gefüllte Kauf-Limits werden um 00:00 UTC mit der gefüllten Menge weitergeführt.

## Befehle auf dem Server
```bash
bash ~/tradelab-signal-bot/auto_bot/install.sh      # einmalig
nano ~/.tradelab.env                                # Schlüssel eintragen (nur du!)
python3 ~/tradelab-signal-bot/auto_bot/bot.py --test-order   # Demo-Testorder setzen und löschen
sudo systemctl start tradelab-bot                   # starten
journalctl -u tradelab-bot -f                       # Logs live anschauen
bash ~/tradelab-signal-bot/auto_bot/update.sh       # neuen Code einspielen
```

## Echtes Geld (später, nur nach Bestätigung)
In `einstellungen.json` `"modus": "live"` und `"max_einsatz_usdt"` > 0 setzen, **und** in `~/.tradelab.env` `LIVE_BESTAETIGT=JA` eintragen. Dazu neue **Live**-API-Schlüssel ohne Abhebe-Recht, mit Server-IP.

## Demo über Alpaca Paper (läuft auf GitHub, 0 Fr.)

- Workflow `.github/workflows/auto_bot.yml`, gestartet alle 10 Min. von cron-job.org (Reserve: GitHub-Zeitplan).
- Secrets: `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL` (paper-api), `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
- Nur Paper erlaubt: andere URL → Bot bricht ab. Live-Geld später nur über OKX.
- Alpaca hat kein OCO für Krypto: Stop liegt als stop_limit beim Broker, Ziel prüft der Bot alle 10 Min.
- Journal rechnet immer echte Alpaca-Gebühren (0,15 % Maker / 0,25 % Taker) ein.
- Test: Actions → "Auto-Bot (Alpaca Paper)" → Run workflow → Haken "Nur Test-Order".
- Zustand + Journal: `auto_bot/daten/` (wird nach jedem Lauf committet).

## P4 – 5 Strategien als Simulation (aktiv seit 15.11.2026)

- Datei `p4_bot.py`, Einstellungen `p4_einstellungen.json`, Daten `daten_p4/`.
- Strategien: BTC Long (SMA 50), ETH Long (SMA 50), ETH Short (SMA 20), ADA Short (SMA 20), SOL Long (SMA 20).
- Regeln wie V3: Limit = Eröffnung ∓ 0.3 ATR, aktiv 07:45 CH bis 20:00 UTC, Stop 0.8 ATR, Ziel 2R, 48 h, max 2 offen pro Strategie.
- Simulation: füllt Orders anhand echter 5-Min-Kerzen von Binance (wie Backtest), Gebühren OKX-Perpetual 0.09 %.
- Start 100, Risiko 3 % pro Trade. Not-Aus bei −40 % vom Höchststand.
- Backtest ab Jul 2024: ca. 5 Trades/Woche, PF 1.33, max. Rückgang 10.8 R.
- Test: `python tests/p4_replay.py <ordner_mit_pickles> 2026-06-01 2026-10-31` (Bot-Replay = Backtest ±1 Trade).
- Der Alpaca-V3-Bot (`bot.py`) ist pausiert; Code und Secrets bleiben für später.

## P4 Demo (aktiv seit 15.11.2026) – `p4_demo.py`

- Longs (BTC, ETH, SOL): echte Orders im **Alpaca-Paper**-Konto (Limit-Kauf, Stop-Limit beim Broker, Ziel + 48 h durch den Bot).
- Shorts (ETH, ADA): Simulation mit Live-Kursen. Grund: Alpaca kann bei Krypto nicht shorten; Bybit sperrt die USA (403),
  Binance-Demo sperrt die USA (451), Kraken-Futures-Demo gibt es nicht mehr, OKX-Demo braucht einen EU-Server (kostet).
- Gemeinsames Demo-Konto `daten_demo/konto.json` (Start 100 Fr., Risiko 3 %), Journal `daten_demo/journal.csv`,
  Gewinne immer nach OKX-Perpetual-Gebühren (0.02 % Maker / 0.05 % Taker).
- Vergleichs-Simulation aller 5 Strategien weiter in `daten_p4/`.
- Telegram: nur Einstieg, Ausstieg, Wochenbericht (So 19:00), Not-Aus (−40 %), Fehler.
- Tests: `tests/test_demo_long.py`, `tests/test_alpaca.py`, `DEMO=1 tests/p4_replay.py <pickles> VON BIS`.
