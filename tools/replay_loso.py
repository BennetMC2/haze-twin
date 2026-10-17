"""Leave-one-season-out check of the range calibration: fit the widening on two replayed seasons,
test how often NEA's readings fall inside the 90% range on the third. Writes history/replay/loso.json.
The stored replay forecasts are raw (uncalibrated), so nothing is applied twice."""
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SGT = timezone(timedelta(hours=8))
GROUPS = ["days 1-3", "days 4-6", "days 7-15"]


def load_rows():
    daily = json.loads((ROOT / "history" / "psi_daily.json").read_text())["daily"]
    rows = {}
    for y in sorted(int(f.stem) for f in (ROOT / "history" / "replay").glob("[0-9][0-9][0-9][0-9].json")):
        for issue, f in json.loads((ROOT / "history" / "replay" / f"{y}.json").read_text()).items():
            d0 = date.fromisoformat(issue)
            for d in f["days"]:
                vd = datetime.fromisoformat(d["iso"].replace("Z", "+00:00")).astimezone(SGT).date()
                lead = (vd - d0).days
                o = daily.get(vd.isoformat())
                if o is None or lead < 1:
                    continue
                g = 0 if lead <= 3 else 1 if lead <= 6 else 2
                rows.setdefault((y, g), []).append((o, d))
    return rows


def widen(d, klo, khi, q):
    m = math.log(max(1, d["p50"]))
    v = math.log(max(1, d[q]))
    return min(500, math.exp(m + (klo if v < m else khi) * (v - m)))


def misses(rs, klo, khi):
    n = len(rs)
    below = sum(o < widen(d, klo, khi, "p5") for o, d in rs) / n
    above = sum(o > widen(d, klo, khi, "p95") for o, d in rs) / n
    return below, above


def fit(rs):
    grid = [x / 20 for x in range(16, 60)]
    return min(((klo, khi) for klo in grid for khi in grid),
               key=lambda k: sum((x - 0.05) ** 2 for x in misses(rs, *k)))


def main():
    rows = load_rows()
    out = []
    for g in range(3):
        for hold in sorted({y for (y, _) in rows}):
            train = [x for (y, gg), v in rows.items() if gg == g and y != hold for x in v]
            if (hold, g) not in rows:
                continue
            test = rows[(hold, g)]
            klo, khi = fit(train)
            b, a = misses(test, klo, khi)
            b0, a0 = misses(test, 1, 1)
            out.append({"lead": GROUPS[g], "held_out": hold, "n": len(test), "fit_lo": klo, "fit_hi": khi,
                        "inside90": round(100 * (1 - a - b)), "inside90_raw": round(100 * (1 - a0 - b0)),
                        "below": round(100 * b), "above": round(100 * a)})
            print(out[-1])
    (ROOT / "history" / "replay" / "loso.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
