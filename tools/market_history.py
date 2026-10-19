"""Price history for the prediction-market view: each forecast's yes/no odds as issued.

Reads history/runs.json (each entry's "markets", added by engine/run.js) and writes
site/data/market_history.json: {"labels": {key: question}, "points": [[issued, {key: percent}], ...]}.
Usage: python tools/market_history.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "above200_6": "PSI above 200 in the next 6 days?",
    "hazardous6": "PSI above 300 in the next 6 days?",
    "hazardous15": "PSI above 300 in the next 15 days?",
    "below100_15": "Back to 100 or below within 15 days?",
    "over_1 Nov": "Haze season over by 1 Nov?",
    "over_15 Nov": "Haze season over by 15 Nov?",
    "over_1 Dec": "Haze season over by 1 Dec?",
    "over_15 Dec": "Haze season over by 15 Dec?",
}


def markets_of(fc_like):
    """Pull the market numbers out of a forecast (or a stored run entry)."""
    n = (fc_like.get("regions") or {}).get("national") or {}
    m = n.get("markets") or {}
    out = {k: round(m[k], 1) for k in ("above200_6", "hazardous6", "hazardous15", "below100_15") if isinstance(m.get(k), (int, float))}
    for d in (fc_like.get("season") or {}).get("dates") or []:
        k = "over_" + d["label"]
        if k in LABELS:
            out[k] = d["p"]
    return out


def build():
    runs = json.loads((ROOT / "history/runs.json").read_text())
    pts = [[r.get("issued") or r["t0"], r["markets"]] for r in runs if r.get("markets") and not r.get("sample")]
    pts.sort(key=lambda p: p[0])
    (ROOT / "site/data/market_history.json").write_text(json.dumps({"labels": LABELS, "points": pts}))
    print("market history:", len(pts), "points")


if __name__ == "__main__":
    build()
