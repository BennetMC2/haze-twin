"""Fire hotspots from NASA FIRMS (VIIRS NOAA-20 and NOAA-21, near real time).

Docs: https://firms.modaps.eosdis.nasa.gov/api/area/
Needs FIRMS_MAP_KEY. Suomi-NPP is tried too until it stops on 1-2 Nov 2026.
"""
import csv
import io
import os
from datetime import datetime, timezone

from net import get
from config import WEST, SOUTH, EAST, NORTH

SOURCES = [("VIIRS_NOAA21_NRT", "N21"), ("VIIRS_NOAA20_NRT", "N20"), ("VIIRS_SNPP_NRT", "SNPP")]


def fetch(t0, days=2, log=print):
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        raise RuntimeError("FIRMS_MAP_KEY is not set")
    area = f"{WEST:g},{SOUTH:g},{EAST:g},{NORTH:g}"
    out = []
    status = {}
    for src, code in SOURCES:
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{area}/{days}"
        try:
            r = get(url, timeout=120, retries=3, min_interval=1.0)
        except Exception as e:
            status[code] = f"failed: {str(e).replace(key, '***')[:120]}"
            log(f"  FIRMS {src}: {status[code]}")
            continue
        text = r.text
        if text.lower().startswith("invalid") or "error" in text[:80].lower():
            status[code] = "failed: " + text[:80].replace(key, "***")
            log(f"  FIRMS {src}: {status[code]}")
            continue
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            try:
                conf = (row.get("confidence") or "").strip().lower()
                if conf in ("l", "low"):
                    continue
                d = row["acq_date"]
                hhmm = row["acq_time"].zfill(4)
                t = datetime.strptime(d + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
                hours_ago = (t0 - t).total_seconds() / 3600.0
                if hours_ago < -1 or hours_ago > 48:
                    continue
                out.append({"lon": float(row["longitude"]), "lat": float(row["latitude"]),
                            "frp": float(row.get("frp") or 0.0), "h": max(0.0, hours_ago),
                            "sat": code, "conf": conf[:1] or "n"})
                n += 1
            except (KeyError, ValueError):
                continue
        status[code] = f"ok, {n} detections"
        log(f"  FIRMS {src}: {n} detections")
    if not any(s.startswith("ok") for s in status.values()):
        raise RuntimeError("no FIRMS source succeeded: " + "; ".join(f"{k} {v}" for k, v in status.items()))
    return out, status


def to_fire_list(dets):
    """Average FRP across satellites so fires seen by two satellites are not double-counted.

    Returns list of (lon, lat, frp, hours_ago) for trajectories.make_fire_cells.
    """
    sats = {d["sat"] for d in dets if d["sat"] in ("N20", "N21", "SNPP")}
    n = max(1, len(sats))
    return [(d["lon"], d["lat"], d["frp"] / n, d["h"]) for d in dets]
