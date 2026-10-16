"""Hourly refresh: put NEA's latest readings into the last full run's inputs, so the odds re-anchor to the
newest PSI without re-downloading weather and fires. Exits 3 if there is nothing new (skip the rest).
Usage: python tools/refresh_obs.py site/data/inputs.json"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import fetch_nea
import build_inputs as B

p = Path(sys.argv[1])
inp = json.loads(p.read_text())
t0 = datetime.fromisoformat(inp["t0"])
nea = fetch_nea.fetch(days=2, log=print)
latest = nea["latest_time"]
if inp["obs"].get("time") and datetime.fromisoformat(latest) <= datetime.fromisoformat(inp["obs"]["time"]):
    print("no newer NEA reading than", inp["obs"]["time"])
    sys.exit(3)
obs = B.obs_block(nea, t0)
obs["time"] = latest
inp["obs"] = obs
inp["refreshed"] = datetime.now(timezone.utc).isoformat()
p.write_text(json.dumps(inp, separators=(",", ":")))
print("re-anchored to NEA reading at", latest, "PSI", obs["psi_now"].get("national"))
