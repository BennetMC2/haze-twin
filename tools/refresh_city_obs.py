"""Hourly refresh for the home cities: put each city's latest WAQI reading into its saved inputs, at the
reading's real time, so the odds re-anchor without re-running the weather and back-traces.
Usage: python tools/refresh_city_obs.py site/data/cities"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import fetch_city

root = Path(sys.argv[1] if len(sys.argv) > 1 else "site/data/cities")
idx = json.loads((root / "index.json").read_text()) if (root / "index.json").exists() else {"cities": []}
for c in idx.get("cities", []):
    p = root / c["id"] / "inputs.json"
    if not p.exists():
        continue
    inp = json.loads(p.read_text())
    r = fetch_city.reading(inp["city"])
    if not r or r.get("pm25") is None:
        continue
    t0 = datetime.fromisoformat(inp["t0"])
    new = fetch_city.obs_block(r, t0)
    if new["time"] <= inp["obs"].get("time", ""):
        print(c["name"], "no newer reading")
        continue
    inp["obs"] = new
    p.write_text(json.dumps(inp, separators=(",", ":")))
    print(c["name"], "re-anchored to", new["time"], "PSI", new["psi_now"]["national"])
