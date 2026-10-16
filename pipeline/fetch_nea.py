"""Singapore PSI and PM2.5 from NEA via data.gov.sg (v2 real-time API).

Docs: https://guide.data.gov.sg/developer-guide/real-time-apis
No key is needed at low volume (6 calls per 10 s). If DATAGOVSG_API_KEY is set it is
sent in the x-api-key header for higher limits.
"""
import os
from datetime import datetime, timedelta, timezone

from net import get

BASE = "https://api-open.data.gov.sg/v2/real-time/api/"
SGT = timezone(timedelta(hours=8))


def _headers():
    k = os.environ.get("DATAGOVSG_API_KEY")
    return {"x-api-key": k} if k else None


def _items(endpoint, date=None):
    params = {"date": date} if date else None
    out = []
    token = None
    for _ in range(30):
        p = dict(params or {})
        if token:
            p["paginationToken"] = token
        r = get(BASE + endpoint, headers=_headers(), params=p or None, min_interval=1.8, host_key="datagovsg")
        js = r.json()
        data = js.get("data") or {}
        out.extend(data.get("items") or [])
        token = data.get("paginationToken")
        if not token:
            break
    return out


def _flatten(items):
    """-> list of (datetime, {reading_key: {region: value}})"""
    rows = []
    for it in items:
        ts = it.get("timestamp") or it.get("updatedTimestamp")
        if not ts:
            continue
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        rd = {}
        for k, v in (it.get("readings") or {}).items():
            if isinstance(v, dict):
                rd[k.lower()] = {rk.lower(): float(rv) for rk, rv in v.items() if isinstance(rv, (int, float))}
        rows.append((t, rd))
    rows.sort(key=lambda x: x[0])
    return rows


def _pick(rd, *names):
    for n in names:
        if n in rd:
            return rd[n]
    for k in rd:
        for n in names:
            if n.replace("_", "") in k.replace("_", ""):
                return rd[k]
    return None


def fetch(days=7, log=print):
    """Return dict with latest readings and hourly history (UTC times)."""
    now_sgt = datetime.now(SGT)
    psi_rows, pm_rows = [], []
    for d in range(days, -1, -1):
        day = (now_sgt - timedelta(days=d)).strftime("%Y-%m-%d")
        try:
            psi_rows += _flatten(_items("psi", day))
        except Exception as e:  # keep going with what we have
            log(f"  NEA PSI {day}: {e}")
        try:
            pm_rows += _flatten(_items("pm25", day))
        except Exception as e:
            log(f"  NEA PM2.5 {day}: {e}")
    if not psi_rows:
        psi_rows = _flatten(_items("psi"))
    if not pm_rows:
        pm_rows = _flatten(_items("pm25"))
    psi_hist, pm24_hist, pm1_hist = [], [], []
    seen = set()
    for t, rd in psi_rows:
        if t in seen:
            continue
        seen.add(t)
        psi = _pick(rd, "psi_twenty_four_hourly", "psi_24h")
        pm24 = _pick(rd, "pm25_twenty_four_hourly", "pm25_24h")
        if psi:
            psi_hist.append({"t": t.isoformat(), **psi})
        if pm24:
            pm24_hist.append({"t": t.isoformat(), **pm24})
    seen = set()
    for t, rd in pm_rows:
        if t in seen:
            continue
        seen.add(t)
        pm1 = _pick(rd, "pm25_one_hourly", "pm25_1h")
        if pm1:
            pm1_hist.append({"t": t.isoformat(), **pm1})
    for h in (psi_hist, pm24_hist, pm1_hist):
        for row in h:
            if "national" not in row:
                vals = [row[r] for r in ("north", "south", "east", "west", "central") if r in row]
                if vals:
                    row["national"] = max(vals)
    if not psi_hist:
        raise RuntimeError("NEA returned no PSI readings")
    log(f"  NEA: {len(psi_hist)} PSI hours, {len(pm24_hist)} 24-h PM2.5 hours, {len(pm1_hist)} 1-h PM2.5 hours")
    return {"psi_hist": psi_hist, "pm24_hist": pm24_hist, "pm1_hist": pm1_hist,
            "latest_time": psi_hist[-1]["t"]}
