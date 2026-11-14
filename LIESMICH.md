# TradeLab V3 – Signal-Wächter (Cloud, 24/7)

Läuft automatisch auf GitHub Actions, jede Stunde, auch wenn dein Laptop aus ist.
Schickt Hinweise per Telegram. Er gibt **keine Orders** auf.

## Strategie: Rücksetzer nur Long, Ziel 2R (BTCUSDT)
1. **Trend:** Tages-Schluss von gestern liegt über SMA 20 (Tage). Sonst gibt es heute keinen Trade.
2. **Kauf-Limit:** Tageseröffnung (00:00 UTC) minus 0,3 × ATR(14 Tage). Das Limit gilt bis Tagesende.
3. **Stop:** Einstieg minus 0,8 × ATR.
4. **Ziel:** Einstieg plus 2 × Stopabstand (2R).
5. **Zeit-Ausstieg:** Nach 48 Stunden ohne Stop oder Ziel zum Marktpreis verkaufen.
6. **Risiko:** 0,25 % vom Konto pro Trade. Steht in `einstellungen.json`.

Backtest BTC 2021–2026 mit Limit-Einstieg und Gebühren, Prüfung auf neuen Daten ab Juli 2024:
ca. 49 % Treffer, Profit-Faktor 1,17, +0,075 R pro Trade, ca. 2 Trades pro Woche.
Bis zu 8 Verluste in Folge sind möglich. Keine Garantie.

## Telegram-Nachrichten
- 📅 **Tagesplan**, jeden Tag ca. 01:10 Uhr (CH): Er enthält die Limit-Order oder "heute kein Trade".
- 🟢 **Gekauft:** Das Limit wurde ausgelöst.
- ✅ / ❌ **Ziel, Stop oder 48h:** Der Trade wurde geschlossen.
- 📊 **Wochenbericht:** kommt sonntags.

## Dateien
- `journal.csv`: alle Trades der Strategie V3
- `zustand.json`: Konto, offene Trades, Tagespläne
- `journal_v2.csv` / `zustand_v2_backup.json`: alte Donchian-Strategie V2 (Backup)
- Telegram-Token und Chat-ID liegen als Secrets, nicht im Code.
- Manuell starten: Actions → Signal-Waechter → Run workflow
