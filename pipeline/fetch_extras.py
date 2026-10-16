"""Extra free sources that need no keys. Each one fails on its own without stopping the run.

- Airport visibility near the fires and in Singapore (METAR, aviationweather.gov). Haze shows up as
  low visibility with HZ (haze) or FU (smoke) in the weather string.
- CAMS smoke and PM2.5 forecast (Copernicus, via the Open-Meteo air-quality API), as a second opinion.
- News about fires and haze (GDELT 2.0 DOC API), English, Indonesian and Malay.
"""
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
            params={"query": NEWS_QUERY, "mode": "artlist", "maxrecords": 40, "format": "json",
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
                    "time": t})
        if len(out) >= 15:
            break
    log(f"  news: {len(out)} recent articles")
    return out


def fetch_all(log=print):
    extras, sources = {}, []
    for key, fn, name in (("airports", metar, "Airport visibility (METAR)"),
                          ("cams", cams, "CAMS smoke forecast"),
                          ("news", news, "News (GDELT)")):
        try:
            extras[key] = fn(log=log)
            sources.append({"name": name, "status": "ok", "time": datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            log(f"  {name} failed: {e}")
            sources.append({"name": name, "status": "failed", "detail": str(e)[:160]})
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
    return {"airports": air, "cams": {"pm25": cams_pm, "model": "CAMS (sample)"}, "news": [
        {"title": "Sample headline: haze expected to persist over the weekend", "url": None, "domain": "sample", "lang": "English",
         "time": t0.isoformat()}]}
