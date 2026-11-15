# -*- coding: utf-8 -*-
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime, timezone
from strategie import ch_offset, ch_uhr_utc, runde_ab, runde_preis, tagesplan, menge_berechnen
from bot import STANDARD_CFG

def ts(s): return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp())

def test_zeitzonen():
    assert ch_offset(ts("2026-01-15T12:00")) == 1
    assert ch_offset(ts("2026-07-15T12:00")) == 2
    assert ch_offset(ts("2026-03-29T00:59")) == 1 and ch_offset(ts("2026-03-29T01:00")) == 2
    assert ch_offset(ts("2026-10-25T00:59")) == 2 and ch_offset(ts("2026-10-25T01:00")) == 1
    assert ch_uhr_utc(ts("2026-01-15T00:00"), "07:45") == ts("2026-01-15T06:45")
    assert ch_uhr_utc(ts("2026-07-15T00:00"), "07:45") == ts("2026-07-15T05:45")

def test_runden():
    assert runde_ab(0.123456789, "0.00000001") == 0.12345678
    assert runde_ab(0.000099, "0.00001") == 0.00009
    assert runde_preis(65432.16, "0.1") == 65432.2

def test_plan_und_menge():
    tage = [dict(t=i * 86400, o=100.0, h=110.0, l=90.0, c=100.0, fertig=True) for i in range(30)]; tage[-1]["c"] = 101.0
    tage.append(dict(t=30 * 86400, o=130.0, h=131.0, l=129.0, c=130.0, fertig=False))
    p = tagesplan(tage, STANDARD_CFG)
    assert p["trend"] and abs(p["atr"] - 20.0) < 1e-9            # TR = 20 jeden Tag
    assert abs(p["limit"] - (130 - 0.3 * 20)) < 1e-9 and abs(p["abstand"] - 16) < 1e-9
    m, r = menge_berechnen(5000, 16, STANDARD_CFG, {"lotSz": "0.00000001", "minSz": "0.00001"})
    assert abs(r - 12.5) < 1e-9 and abs(m - 0.78125) < 1e-8
    m2, _ = menge_berechnen(5000, 16, STANDARD_CFG, {"lotSz": "0.00000001", "minSz": "0.00001"}, max_einsatz=50, preis=124)
    assert abs(m2 - runde_ab(50 / 124, "0.00000001")) < 1e-12
    tage[-2]["c"] = 1.0                                           # Schluss unter SMA -> kein Trade
    assert not tagesplan(tage, STANDARD_CFG)["trend"]

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("OK", n)
