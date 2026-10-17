"""What we said a day ahead vs what NEA measured, for the page's scorecard.

Reads history/runs.json (every forecast as issued) and history/obs.json (NEA's noon 24-hour PSI per day),
writes site/data/scorecard.json. For each measured day, the forecast used is the latest one issued at least
12 hours before that noon (the evening before or earlier), so it is a genuine forecast, not a nowcast.
Usage: python tools/scorecard.py
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build():
    runs = json.loads((ROOT / "history/runs.json").read_text())
    obs = json.loads((ROOT / "history/obs.json").read_text())
    out = []
    for key in sorted(obs)[-7:]:
        noon = datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc)
        measured = (obs[key] or {}).get("national")
        if measured is None:
            continue
        best = None
        for r in runs:
            if r.get("sample"):
                continue
            issued = ts(r.get("issued") or r["t0"])
            if issued > noon - timedelta(hours=12):
                continue
            for d in ((r.get("regions") or {}).get("national") or {}).get("days") or []:
                if abs((ts(d["iso"]) - noon).total_seconds()) < 3 * 3600 and (best is None or issued > best[0]):
                    best = (issued, d)
        if best:
            issued, d = best
            lo, hi = d.get("p5"), d.get("p95")
            out.append({"day": noon.isoformat(), "issued": issued.isoformat(), "said": d.get("p50"), "lo": lo, "hi": hi,
                        "measured": measured, "inside": lo is not None and hi is not None and lo <= measured <= hi})
    (ROOT / "site/data/scorecard.json").write_text(json.dumps({"built": datetime.now(timezone.utc).isoformat(), "days": out}))
    print("scorecard:", len(out), "days")


if __name__ == "__main__":
    build()
