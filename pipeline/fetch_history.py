"""NEA PSI history (2014 onwards) and past-season analogs.

1. Downloads NEA's "Historical 24-hr PSI" dataset from data.gov.sg (hourly, five regions) once a week.
2. Backfills days after the dataset ends from the real-time API (one call per day, noon reading), cached.
3. Finds past days that looked like today (similar PSI level and trend, haze season months) and
   returns how PSI moved over the following 15 days, as ratios to that day's level.

The engine blends these analogs in more and more at longer leads, so the long range leans on how
real seasons behaved instead of only on the weather forecasts.
"""
import csv
import io
import json
import math
import time
from datetime import datetime, timedelta, timezone, date

from net import get
from config import HISTORY

DATASET = "d_b4cf557f8750260d229c49fd768e11ed"
API = "https://api-open.data.gov.sg/v1/public/api/datasets/"
STORE = HISTORY / "psi_daily.json"         # {"YYYY-MM-DD": national noon 24-h PSI}
SGT = timezone(timedelta(hours=8))
SEASON_YEARS = {2015: "El Niño", 2019: "positive IOD", 2023: "El Niño", 2026: "El Niño"}


def _parse_time(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _download_dataset(log):
    get(API + DATASET + "/initiate-download", timeout=60, retries=1, min_interval=13, host_key="dgs-dl")
    url = None
    for _ in range(12):
        r = get(API + DATASET + "/poll-download", timeout=60, retries=1, min_interval=13, host_key="dgs-dl")
        data = (r.json() or {}).get("data") or {}
        if data.get("url"):
            url = data["url"]
            break
        time.sleep(5)
    if not url:
        raise RuntimeError("dataset download link not ready")
    text = get(url, timeout=180, retries=2).text
    daily = {}
    rd = csv.DictReader(io.StringIO(text))
    for row in rd:
        keys = {k.lower().strip(): v for k, v in row.items() if k}
        tcol = next((k for k in keys if "psi" in k or "time" in k or "date" in k), None)
        t = _parse_time(keys.get(tcol, "")) if tcol else None
        if t is None or t.hour != 12:
            continue
        vals = []
        for r_ in ("north", "south", "east", "west", "central"):
            try:
                vals.append(float(keys[r_]))
            except (KeyError, ValueError):
                pass
        if vals:
            daily[t.strftime("%Y-%m-%d")] = max(vals)
    log(f"  history: {len(daily)} days from the NEA dataset")
    return daily


def _backfill(daily, log, max_days=90):
    """Noon readings for days missing between the dataset's end and yesterday."""
    today = datetime.now(SGT).date()
    start = today - timedelta(days=max_days)
    added = 0
    d = start
    while d < today:
        k = d.isoformat()
        if k not in daily:
            try:
                r = get("https://api-open.data.gov.sg/v2/real-time/api/psi", params={"date": f"{k}T12:00:00"},
                        timeout=40, retries=1, min_interval=1.8, host_key="datagovsg")
                items = ((r.json() or {}).get("data") or {}).get("items") or []
                if items:
                    rd = {kk.lower(): vv for kk, vv in (items[0].get("readings") or {}).items()}
                    psi = rd.get("psi_twenty_four_hourly") or {}
                    vals = [float(v) for kk, v in psi.items() if kk.lower() in ("north", "south", "east", "west", "central")]
                    if vals:
                        daily[k] = max(vals)
                        added += 1
            except Exception:
                pass
        d += timedelta(days=1)
    log(f"  history: backfilled {added} recent days")


def load(log=print, refresh_days=7):
    try:
        store = json.loads(STORE.read_text())
    except Exception:
        store = {"daily": {}, "dataset_fetched": None}
    daily = store.get("daily", {})
    fetched = store.get("dataset_fetched")
    stale = not fetched or (datetime.now(timezone.utc) - datetime.fromisoformat(fetched)).days >= refresh_days
    if stale or len(daily) < 1000:
        try:
            daily.update(_download_dataset(log))
            store["dataset_fetched"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            log(f"  history: dataset download failed ({e}); using {len(daily)} stored days")
    _backfill(daily, log)
    store["daily"] = dict(sorted(daily.items()))
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(store, separators=(",", ":")))
    return store["daily"]


def analogs(daily, psi_now, psi_2d_ago, today, horizon=15, keep=60):
    """Past days similar to today and how PSI moved afterwards (ratios to that day)."""
    if not psi_now or psi_now <= 0:
        return None
    trend_now = (psi_now - psi_2d_ago) if psi_2d_ago else 0.0
    days = sorted(daily)
    vals = {k: daily[k] for k in days}
    cands = []
    for k in days:
        d0 = date.fromisoformat(k)
        if d0.month not in (8, 9, 10, 11):
            continue
        if (today - d0).days < horizon + 1:
            continue
        p0 = vals[k]
        if psi_now > 100 and p0 < 85:
            continue
        if psi_now <= 100 and p0 > 130:
            continue
        p2 = vals.get((d0 - timedelta(days=2)).isoformat())
        path, last, have = [], p0, 0
        for j in range(1, horizon + 1):
            v = vals.get((d0 + timedelta(days=j)).isoformat())
            if v is not None:
                last, have = v, have + 1
            path.append(round(last / p0, 3))
        if have < horizon * 0.7:
            continue
        w = math.exp(-0.5 * ((math.log(p0) - math.log(psi_now)) / 0.25) ** 2)
        if p2:
            w *= math.exp(-0.5 * (((p0 - p2) - trend_now) / 40.0) ** 2)
        if d0.year in SEASON_YEARS:
            w *= 1.5
        cands.append((w, k, path))
    if not cands:
        return None
    cands.sort(reverse=True)
    cands = cands[:keep]
    years = sorted({int(k[:4]) for _, k, _ in cands})
    return {"paths": [p for _, _, p in cands], "w": [round(w, 4) for w, _, _ in cands],
            "dates": [k for _, k, _ in cands], "years": years}


def sample_analogs():
    """Synthetic analogs for sample mode: a 2015-like season that lingers, then ends."""
    import random
    rnd = random.Random(4)
    paths, w, dates = [], [], []
    for i in range(40):
        level = 1.0
        end = rnd.randint(6, 30)
        p = []
        for j in range(1, 16):
            level *= math.exp(rnd.gauss(0, 0.12))
            if j >= end:
                level *= 0.7
            p.append(round(max(0.25, min(2.2, level)), 3))
        paths.append(p)
        w.append(1.0)
        dates.append(f"2015-09-{(i % 28) + 1:02d}")
    return {"paths": paths, "w": w, "dates": dates, "years": [2015, 2019], "sample": True}
