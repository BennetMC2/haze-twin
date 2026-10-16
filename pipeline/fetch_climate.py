"""Climate drivers for the season outlook: El Niño (RONI) and the Indian Ocean Dipole (DMI).

RONI: NOAA CPC relative Oceanic Niño Index (official ENSO index since Feb 2026), 3-month seasons.
DMI:  NOAA PSL monthly Dipole Mode Index (HadISST).
If a source can't be reached, the last good value saved in history/climate.json is used.
"""
import json
from datetime import datetime, timezone

from net import get
from config import HISTORY

RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"
STORE = HISTORY / "climate.json"
# Seed values from public statements, used only until the first successful fetch.
SEED = {"roni": 1.69, "roni_period": "JAS 2026", "dmi": 0.44, "dmi_period": "Jul 2026", "source": "seed"}


def _roni():
    text = get(RONI_URL, timeout=60, retries=2).text
    rows = [ln.split() for ln in text.splitlines() if ln.strip()]
    rows = [r for r in rows if len(r) >= 3 and r[1].isdigit()]
    last = rows[-1]
    return float(last[-1]), f"{last[0]} {last[1]}"


def _dmi():
    text = get(DMI_URL, timeout=60, retries=2).text
    best = None
    for ln in text.splitlines():
        p = ln.split()
        if len(p) == 13 and p[0].isdigit():
            year = int(p[0])
            for m, v in enumerate(p[1:], start=1):
                x = float(v)
                if x > -90:
                    best = (x, f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m-1]} {year}")
    if best is None:
        raise RuntimeError("no DMI values")
    return best


def fetch(log=print):
    try:
        stored = json.loads(STORE.read_text())
    except Exception:
        stored = dict(SEED)
    out = dict(stored)
    status = []
    try:
        out["roni"], out["roni_period"] = _roni()
        status.append(f"RONI {out['roni']:+.2f} ({out['roni_period']})")
    except Exception as e:
        status.append(f"RONI kept at {out.get('roni')} ({e.__class__.__name__})")
    try:
        out["dmi"], out["dmi_period"] = _dmi()
        status.append(f"DMI {out['dmi']:+.2f} ({out['dmi_period']})")
    except Exception as e:
        status.append(f"DMI kept at {out.get('dmi')} ({e.__class__.__name__})")
    out["checked"] = datetime.now(timezone.utc).isoformat()
    out["source"] = "fetched" if not any("kept" in s for s in status) else out.get("source", "seed")
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(out, indent=1))
    except Exception:
        pass
    log("  climate: " + "; ".join(status))
    return out, "; ".join(status)
