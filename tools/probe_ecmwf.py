"""Time and check the ECMWF open-data downloads (physics IFS ensemble and the AI ensemble, AIFS ENS)."""
import sys
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import fetch_ecmwf

out = Path("history/probe_ecmwf.txt")
lines = []
for model in sys.argv[1:] or ["aifs-ens", "ifs"]:
    t = time.time()
    try:
        e = fetch_ecmwf.fetch(log=lambda m: lines.append("   " + str(m)), model=model)
        U = e["U"]
        lines.append(f"{model}: run {e['run']:%Y-%m-%d %H}Z, U {U.shape}, NaN {np.isnan(U).mean():.3f}, "
                     f"mean |wind| {np.nanmean(np.hypot(U, e['V'])):.2f} m/s, rain mean {np.nanmean(e['R']):.3f} mm/6h, "
                     f"{time.time() - t:.0f}s")
    except Exception:
        lines.append(f"{model}: FAILED after {time.time() - t:.0f}s\n" + traceback.format_exc()[-1500:])
    print(lines[-1], flush=True)
out.write_text("\n".join(lines) + "\n")
