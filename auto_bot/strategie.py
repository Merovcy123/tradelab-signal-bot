# -*- coding: utf-8 -*-
"""Strategie V3 "Rücksetzer nur Long, Ziel 2R" – reine Rechenfunktionen (ohne Netzwerk)."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

TAG = 86400


def ch_offset(ts):
    """Stunden Schweizer Zeit vor UTC (MEZ 1 / MESZ 2; Wechsel letzter Sonntag März/Oktober 01:00 UTC)."""
    d = datetime.fromtimestamp(ts, timezone.utc)

    def letzter_sonntag(jahr, monat):
        x = datetime(jahr, monat + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
        return x - timedelta(days=(x.weekday() + 1) % 7)

    beginn = letzter_sonntag(d.year, 3).replace(hour=1)
    ende = letzter_sonntag(d.year, 10).replace(hour=1)
    return 2 if beginn <= d < ende else 1


def zeit_ch(ts, fmt="%d.%m. %H:%M"):
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(hours=ch_offset(ts))).strftime(fmt)


def ch_uhr_utc(tag_ts, hhmm):
    """UTC-Zeitstempel für eine Schweizer Uhrzeit (z. B. "07:45") am UTC-Tag tag_ts."""
    h, m = (int(x) for x in hhmm.split(":"))
    return tag_ts + (h - ch_offset(tag_ts + 12 * 3600)) * 3600 + m * 60


def runde_ab(wert, schritt):
    """Auf Schrittweite abrunden (Menge)."""
    s = Decimal(str(schritt))
    return float((Decimal(str(wert)) / s).to_integral_value(ROUND_DOWN) * s)


def runde_preis(wert, tick):
    t = Decimal(str(tick))
    return float((Decimal(str(wert)) / t).to_integral_value(ROUND_HALF_UP) * t)


def tagesplan(tage, cfg):
    """tage: Tageskerzen [{t,o,h,l,c,fertig}] inkl. heutiger (laufender) Kerze als letzte.
    Nutzt nur abgeschlossene Tage + heutige Eröffnung (kein Blick in die Zukunft)."""
    heute = tage[-1]
    alt = [x for x in tage[:-1] if x["fertig"]]
    n_sma, n_atr = cfg["sma_tage"], cfg["atr_tage"]
    if len(alt) < max(n_sma, n_atr) + 1:
        raise ValueError("zu wenig Tageskerzen")
    schluss = alt[-1]["c"]
    sma = sum(x["c"] for x in alt[-n_sma:]) / n_sma
    trs = []
    for i in range(len(alt) - n_atr, len(alt)):
        x, v = alt[i], alt[i - 1]
        trs.append(max(x["h"] - x["l"], abs(x["h"] - v["c"]), abs(x["l"] - v["c"])))
    atr = sum(trs) / n_atr
    plan = dict(tag=heute["t"], eroeffnung=heute["o"], gestern_schluss=schluss, sma=sma, atr=atr,
                trend=schluss > sma, start=ch_uhr_utc(heute["t"], cfg["start_ch"]),
                nachricht=ch_uhr_utc(heute["t"], cfg["nachricht_ab_ch"]), ende=heute["t"] + TAG,
                status="wartet" if schluss > sma else "kein_trade", gemeldet=False)
    if plan["trend"]:
        plan["limit"] = heute["o"] - cfg["ruecksetzer_atr"] * atr
        plan["abstand"] = cfg["stop_atr"] * atr
    return plan


def menge_berechnen(konto_usdt, abstand, cfg, inst, max_einsatz=None, preis=None):
    """Positionsgrösse: Risiko % vom Konto / Stopabstand, auf lotSz abgerundet. None = zu klein."""
    risiko = konto_usdt * cfg["risiko_prozent"] / 100
    menge = risiko / abstand
    if max_einsatz and preis:
        menge = min(menge, max_einsatz / preis)
    menge = runde_ab(menge, inst["lotSz"])
    if menge < float(inst["minSz"]):
        return None, risiko
    return menge, risiko
