"""Extra free sources that need no keys. Each one fails on its own without stopping the run.

- Airport visibility near the fires and in Singapore (METAR, aviationweather.gov). Haze shows up as
  low visibility with HZ (haze) or FU (smoke) in the weather string.
- CAMS smoke and PM2.5 forecast (Copernicus, via the Open-Meteo air-quality API), as a second opinion.
- News about fires and haze (GDELT 2.0 DOC API), English, Indonesian and Malay, sorted into signals.
- Recent rain over the fire areas (Open-Meteo, last 60 days) as a dryness measure for the season outlook.
- Regional PM2.5 stations (OpenAQ v3), if OPENAQ_API_KEY is set.
"""
import os
import re
from datetime import datetime, timezone

from net import get

AIRPORTS = [
    ("WSSS", "Singapore Changi", 103.99, 1.36),
    ("WIBB", "Pekanbaru", 101.44, 0.46),
    ("WIJJ", "Jambi", 103.64, -1.64),
    ("WIPP", "Palembang", 104.70, -2.90),
    ("WIOO", "Pontianak", 109.40, -0.15),
    ("WAGG", "Palangka Raya", 113.94, -2.23),
    ("WAOO", "Banjarmasin", 114.76, -3.44),
    ("WMKK", "Kuala Lumpur", 101.71, 2.75),
    ("WMKJ", "Johor Bahru", 103.67, 1.64),
    ("WBGG", "Kuching", 110.35, 1.48),
]

NEWS_QUERY = ('(haze OR "kabut asap" OR karhutla OR jerebu OR "forest fires" OR "titik panas") '
              '(Singapore OR Malaysia OR Indonesia OR Sumatra OR Kalimantan OR Riau)')


def metar(log=print):
    ids = ",".join(a[0] for a in AIRPORTS)
    r = get("https://aviationweather.gov/api/data/metar", params={"ids": ids, "format": "json", "hours": 36},
            timeout=60, retries=2)
    rows = r.json()
    latest = {}
    for m in rows:
        icao = m.get("icaoId")
        t = m.get("obsTime") or m.get("reportTime")
        if not icao or t is None:
            continue
        if isinstance(t, (int, float)):
            ts = datetime.fromtimestamp(t, timezone.utc)
        else:
            ts = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        if icao not in latest or ts > latest[icao]["t"]:
            vis = m.get("visib")
            try:
                vis_km = None if vis is None else round(float(str(vis).replace("+", "")) * 1.609, 1)
            except ValueError:
                vis_km = None
            wx = m.get("wxString") or ""
            latest[icao] = {"t": ts, "vis_km": vis_km, "wx": wx}
    out = []
    for icao, name, lon, lat in AIRPORTS:
        if icao in latest:
            d = latest[icao]
            haze = any(code in d["wx"].split() or code in d["wx"] for code in ("HZ", "FU"))
            out.append({"icao": icao, "name": name, "lon": lon, "lat": lat, "time": d["t"].isoformat(),
                        "vis_km": d["vis_km"], "wx": d["wx"], "haze": haze})
    log(f"  airports: {len(out)} with recent reports, {sum(a['haze'] for a in out)} reporting haze or smoke")
    return out


def cams(log=print):
    r = get("https://air-quality-api.open-meteo.com/v1/air-quality",
            params={"latitude": 1.35, "longitude": 103.82, "hourly": "pm2_5,aerosol_optical_depth",
                    "past_days": 2, "forecast_days": 5, "timezone": "UTC"}, timeout=60, retries=2)
    js = r.json()
    h = js.get("hourly") or {}
    times = h.get("time") or []
    pm = h.get("pm2_5") or []
    out = [[t + "Z" if not t.endswith("Z") else t, v] for t, v in zip(times, pm) if v is not None]
    log(f"  CAMS: {len(out)} hourly PM2.5 values for Singapore")
    return {"pm25": out, "model": "CAMS global (via Open-Meteo)"}


def news(log=print):
    r = get("https://api.gdeltproject.org/api/v2/doc/doc",
            params={"query": NEWS_QUERY, "mode": "artlist", "maxrecords": 75, "format": "json",
                    "timespan": "3d", "sort": "datedesc"}, timeout=60, retries=2, min_interval=5.0)
    try:
        arts = r.json().get("articles") or []
    except ValueError:
        arts = []
    seen = set()
    out = []
    for a in arts:
        title = (a.get("title") or "").strip()
        key = title.lower()[:60]
        if not title or key in seen:
            continue
        seen.add(key)
        sd = a.get("seendate") or ""
        try:
            t = datetime.strptime(sd, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            t = None
        out.append({"title": title[:180], "url": a.get("url"), "domain": a.get("domain"), "lang": a.get("language"),
                    "time": t, "tags": classify(title)})
        if len(out) >= 60:
            break
    log(f"  news: {len(out)} recent articles")
    return out


# keyword rules, English / Indonesian / Malay (lower case, matched as substrings)
SIGNALS = {
    "spreading": ["spread", "surge", "worsen", "more hotspots", "hotspots rise", "rising hotspots", "meluas", "bertambah",
                  "meningkat", "melonjak", "titik panas naik", "kian parah", "semakin parah", "bertambah buruk"],
    "emergency": ["emergency", "state of emergency", "siaga darurat", "tanggap darurat", "darurat", "school closure",
                  "schools closed", "sekolah ditutup", "sekolah diliburkan", "evacuat"],
    "suppression": ["water bomb", "water-bomb", "bom air", "cloud seeding", "cloud-seeding", "hujan buatan", "modifikasi cuaca",
                    "tmc", "omc", "extinguish", "padam", "dipadamkan", "under control", "terkendali", "firefighters", "pemadaman"],
    "rain": ["rain", "hujan", "monsoon", "musim hujan", "downpour", "showers"],
    "improving": ["improve", "membaik", "berkurang", "clearer", "clear skies", "psi drops", "psi falls", "haze eases",
                  "turun", "reda", "menurun"],
}
PLACES = {
    "Riau": ["riau", "pekanbaru", "dumai"], "Jambi": ["jambi"], "South Sumatra": ["south sumatra", "sumatera selatan", "sumsel", "palembang", "ogan"],
    "Central Kalimantan": ["central kalimantan", "kalimantan tengah", "kalteng", "palangka"], "South Kalimantan": ["south kalimantan", "kalimantan selatan", "kalsel", "banjar"],
    "West Kalimantan": ["west kalimantan", "kalimantan barat", "kalbar", "pontianak"], "Sarawak": ["sarawak", "kuching"],
    "Singapore": ["singapore"], "Malaysia": ["malaysia", "johor", "selangor", "kuala lumpur"],
}


def classify(title):
    t = title.lower()
    tags = [k for k, words in SIGNALS.items() if any(w in t for w in words)]
    # "rain" inside words like "terrain" or "drain" should not count
    if "rain" in tags and not re.search(r"\b(rain|rains|rainfall|hujan|monsoon|downpour|showers)\b", t):
        tags.remove("rain")
    places = [p for p, words in PLACES.items() if any(w in t for w in words)]
    return {"signals": tags, "places": places}


def signals(articles):
    counts = {k: 0 for k in SIGNALS}
    by_place = {}
    for a in articles:
        tg = a.get("tags") or {}
        for sgl in tg.get("signals", []):
            counts[sgl] += 1
            for p in tg.get("places", []):
                by_place.setdefault(p, {}).setdefault(sgl, 0)
                by_place[p][sgl] += 1
    up = counts["spreading"] + counts["emergency"]
    down = counts["suppression"] + counts["rain"] + counts["improving"]
    total = max(6, up + down)
    pressure = max(-1.0, min(1.0, (up - down) / total))
    return {"counts": counts, "byPlace": by_place, "pressure": round(pressure, 2),
            "growthShift": round(0.02 * pressure, 4), "articles": len(articles)}


DRY_POINTS = {"Kalimantan": [(113.94, -2.23), (114.76, -3.44), (109.4, -0.15)],
              "Sumatra": [(104.7, -2.9), (103.64, -1.64), (101.44, 0.46)]}
NORMAL_RAIN_30D = {"Kalimantan": 160.0, "Sumatra": 140.0}   # rough Sep-Oct norms (mm per 30 days)


def dryness(log=print):
    out = {}
    for isl, pts in DRY_POINTS.items():
        r = get("https://api.open-meteo.com/v1/forecast",
                params={"latitude": ",".join(str(p[1]) for p in pts), "longitude": ",".join(str(p[0]) for p in pts),
                        "daily": "precipitation_sum", "past_days": 60, "forecast_days": 1, "timezone": "UTC"},
                timeout=60, retries=2)
        js = r.json()
        if isinstance(js, dict):
            js = [js]
        tot30, tot60 = [], []
        for loc in js:
            pr = [x or 0 for x in (loc.get("daily") or {}).get("precipitation_sum") or []]
            if len(pr) >= 31:
                tot30.append(sum(pr[-31:-1]))
                tot60.append(sum(pr[-61:-1]))
        if tot30:
            r30 = sum(tot30) / len(tot30)
            out[isl] = {"rain30": round(r30, 1), "rain60": round(sum(tot60) / len(tot60), 1),
                        "index": round(max(0.0, min(1.0, 1 - r30 / NORMAL_RAIN_30D[isl])), 2)}
    log("  dryness: " + ", ".join(f"{k} {v['rain30']} mm in 30 days" for k, v in out.items()))
    return out


def openaq(log=print):
    key = os.environ.get("OPENAQ_API_KEY")
    if not key:
        raise RuntimeError("OPENAQ_API_KEY not set")
    out = []
    now = datetime.now(timezone.utc)
    for page in range(1, 6):
        r = get("https://api.openaq.org/v3/parameters/2/latest", headers={"X-API-Key": key},
                params={"limit": 1000, "page": page}, timeout=60, retries=2, min_interval=1.2)
        res = (r.json() or {}).get("results") or []
        for x in res:
            c = x.get("coordinates") or {}
            lon, lat = c.get("longitude"), c.get("latitude")
            if lon is None or lat is None or not (92 <= lon <= 124 and -10 <= lat <= 10):
                continue
            dt = ((x.get("datetime") or {}).get("utc") or "").replace("Z", "+00:00")
            try:
                t = datetime.fromisoformat(dt)
            except ValueError:
                continue
            if (now - t).total_seconds() > 6 * 3600:
                continue
            v = x.get("value")
            if v is None or v < 0 or v > 1500:
                continue
            out.append({"lon": round(lon, 3), "lat": round(lat, 3), "pm25": round(v, 1), "time": t.isoformat(),
                        "loc": x.get("locationsId")})
        if len(res) < 1000:
            break
    log(f"  OpenAQ: {len(out)} recent PM2.5 readings in the region")
    return out


def fetch_all(log=print):
    extras, sources = {}, []
    for key, fn, name in (("airports", metar, "Airport visibility (METAR)"),
                          ("cams", cams, "CAMS smoke forecast"),
                          ("news", news, "News (GDELT)"),
                          ("dryness", dryness, "Recent rain over fire areas"),
                          ("stations", openaq, "Regional PM2.5 stations (OpenAQ)")):
        try:
            extras[key] = fn(log=log)
            sources.append({"name": name, "status": "ok", "time": datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            log(f"  {name} failed: {e}")
            sources.append({"name": name, "status": "failed", "detail": str(e)[:160]})
    extras["signals"] = signals(extras.get("news") or [])
    return extras, sources


def sample(t0):
    """Placeholder extras for sample mode, clearly fake."""
    from datetime import timedelta
    import math
    air = []
    for i, (icao, name, lon, lat) in enumerate(AIRPORTS):
        vis = [6, 2, 3, 2.5, 4, 1.2, 1.8, 7, 5, 6][i]
        air.append({"icao": icao, "name": name, "lon": lon, "lat": lat, "time": t0.isoformat(), "vis_km": vis,
                    "wx": "HZ" if vis < 5 else "", "haze": vis < 5})
    cams_pm = []
    for h in range(-48, 120):
        t = t0 + timedelta(hours=h)
        cams_pm.append([t.strftime("%Y-%m-%dT%H:%MZ"), round(60 + 35 * math.exp(-((h - 20) / 30) ** 2) - h * 0.15, 1)])
    heads = ["Sample: hotspots surge in Central Kalimantan as dry spell continues",
             "Sample: Riau extends emergency alert status to end of November",
             "Sample: cloud seeding planned over South Sumatra",
             "Sample: Singapore haze expected to persist over the weekend"]
    news_ = [{"title": h, "url": None, "domain": "sample", "lang": "English", "time": t0.isoformat(), "tags": classify(h)} for h in heads]
    stations = [{"lon": 101.45, "lat": 0.5, "pm25": 180, "time": t0.isoformat()}, {"lon": 104.75, "lat": -2.95, "pm25": 140, "time": t0.isoformat()},
                {"lon": 101.7, "lat": 3.1, "pm25": 60, "time": t0.isoformat()}, {"lon": 103.75, "lat": 1.5, "pm25": 75, "time": t0.isoformat()},
                {"lon": 113.9, "lat": -2.2, "pm25": 320, "time": t0.isoformat()}]
    return {"airports": air, "cams": {"pm25": cams_pm, "model": "CAMS (sample)"}, "news": news_, "signals": signals(news_),
            "dryness": {"Kalimantan": {"rain30": 35.0, "rain60": 70.0, "index": 0.78}, "Sumatra": {"rain30": 48.0, "rain60": 95.0, "index": 0.66}},
            "stations": stations}
