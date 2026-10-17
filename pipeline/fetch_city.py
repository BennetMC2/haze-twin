"""Readings for the home cities, from the World Air Quality Index Project (aqicn.org).

The nearest station to each city's centre is used (feed/geo:lat;lon). Its PM2.5 is given as a US AQI
sub-index, which is converted back to PM2.5 (ug/m3) with the US EPA breakpoints. That is closer to a
recent average than a true 24-hour mean, so it is labelled as such on the page.
Free, non-commercial use with attribution to the World Air Quality Index Project and the originating agency.
"""
import os
from datetime import datetime, timezone

import requests

# US EPA PM2.5 breakpoints (2024): (AQI lo, AQI hi, conc lo, conc hi)
US_PM25 = [(0, 50, 0.0, 9.0), (51, 100, 9.1, 35.4), (101, 150, 35.5, 55.4), (151, 200, 55.5, 125.4),
           (201, 300, 125.5, 225.4), (301, 500, 225.5, 325.4)]


def pm_from_us_aqi(aqi):
    if aqi is None or aqi < 0:
        return None
    for a0, a1, c0, c1 in US_PM25:
        if aqi <= a1:
            return round(c0 + (aqi - a0) * (c1 - c0) / (a1 - a0), 1)
    return 325.4


def reading(city, log=print):
    """-> dict(time, pm25, aqi, station, url) or None."""
    token = os.environ.get("WAQI_TOKEN", "").strip()
    if not token:
        log(f"  {city['name']}: WAQI_TOKEN not set")
        return None
    try:
        js = requests.get(f"https://api.waqi.info/feed/geo:{city['lat']};{city['lon']}/", params={"token": token}, timeout=20).json()
    except Exception as e:
        log(f"  {city['name']}: request failed ({type(e).__name__})")
        return None
    d = js.get("data") if isinstance(js, dict) else None
    if not isinstance(js, dict) or js.get("status") != "ok" or not isinstance(d, dict):
        log(f"  {city['name']}: no reading")
        return None
    pm_aqi = ((d.get("iaqi") or {}).get("pm25") or {}).get("v")
    if not isinstance(pm_aqi, (int, float)):
        log(f"  {city['name']}: station has no PM2.5")
        return None
    t = (d.get("time") or {}).get("iso")
    return {"time": t or datetime.now(timezone.utc).isoformat(), "pm25": pm_from_us_aqi(pm_aqi), "aqi": d.get("aqi"),
            "station": (d.get("city") or {}).get("name"), "url": (d.get("city") or {}).get("url"),
            "attribution": [a.get("name") for a in (d.get("attributions") or []) if isinstance(a, dict)][:3]}


def obs_block(r, t0):
    """Observation block in the same shape the engine reads for Singapore (national only)."""
    from build_inputs import BREAK_PSI
    t = datetime.fromisoformat(r["time"].replace("Z", "+00:00"))
    h = round((t - t0).total_seconds() / 3600.0, 2)
    psi = BREAK_PSI(r["pm25"])
    return {"time": t.astimezone(timezone.utc).isoformat(), "pm24_h": h,
            "psi_now": {"national": round(psi)}, "pm24_now": {"national": r["pm25"]}, "pm1_at": {},
            "psi_hist": [[h, {"national": round(psi)}]], "pm24_hist": [[h, r["pm25"]]],
            "station": r.get("station"), "station_url": r.get("url"), "aqi": r.get("aqi"), "source": "waqi",
            "attribution": r.get("attribution")}
