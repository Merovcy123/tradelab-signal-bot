# TradeLab V2 – Signal-Wächter (Cloud, 24/7)

Läuft automatisch auf GitHub Actions – jede Stunde, auch wenn dein Laptop aus ist.
Meldet KAUF-, STOP- und AUSSTIEG-Signale per Telegram. NUR Hinweise, keine Orders.

- Regeln: wie der TradingView-Indikator "TradeLab V2 Trendfolge"
- Märkte: BTC + ETH (4h), AAPL, MSFT, AMZN, SPY (Tageskerzen) – siehe einstellungen.json
- Telegram-Token und Chat-ID liegen als Secrets (Settings → Secrets → Actions), nicht im Code
- Manuell prüfen: Actions → Signal-Waechter → Run workflow
- Verlauf: signale_log.txt
