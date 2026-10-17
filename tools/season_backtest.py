"""Back-test the season-end outlook's climate prior against past years.

For each past year with haze (PSI above 100 on some day from August), the prior's middle date for the end of
the haze season (normal rain onset over Kalimantan/Sumatra, shifted later by El Nino (RONI, JAS) and the Indian
Ocean Dipole (DMI, Aug), plus about a week for fires to die down) is compared with the last day NEA's noon PSI was
above 100. Uses the same constants as engine.js. This tests only the climate prior, not the weather-forecast part.
Writes history/replay/season_backtest.json. Needs network (run in GitHub Actions)."""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from net import get
from fetch_climate import RONI_URL, DMI_URL

ENG = (ROOT / "engine" / "engine.js").read_text()
PRI = json.loads(re.search(r"SEASON_PRIORS = (\{[^}]*\})", ENG).group(1).replace("Kalimantan", '"Kalimantan"').replace("Sumatra", '"Sumatra"'))
LAG_DAYS = 7   # middle of the engine's 4-10 day lag (depends on peat dryness, not known for past years)


def roni_by_year():
    out = {}
    for ln in get(RONI_URL, timeout=60).text.splitlines():
        p = ln.split()
        if len(p) >= 3 and p[0] == "JAS" and p[1].isdigit():
            out[int(p[1])] = float(p[-1])
    return out


def dmi_by_year():
    out = {}
    for ln in get(DMI_URL, timeout=60).text.splitlines():
        p = ln.split()
        if len(p) == 13 and p[0].isdigit() and float(p[8]) > -90:
            out[int(p[0])] = float(p[8])   # August
    return out


def main():
    daily = json.loads((ROOT / "history" / "psi_daily.json").read_text())["daily"]
    roni, dmi = roni_by_year(), dmi_by_year()
    rows = []
    for y in sorted({int(k[:4]) for k in daily}):
        haze = [k for k in sorted(daily) if k.startswith(str(y)) and k[5:] >= "08-01" and daily[k] > 100]
        if not haze or y not in roni:
            continue
        shift = 6 * min(2.5, max(0, roni[y])) + 4 * min(2, max(0, dmi.get(y, 0) / 0.4))
        onset = max(date(y, m + 1, d) for m, d in PRI.values()) + timedelta(days=round(shift))
        pred = onset + timedelta(days=LAG_DAYS)
        actual = date.fromisoformat(haze[-1])
        rows.append({"year": y, "roni_jas": roni[y], "dmi_aug": dmi.get(y), "predicted_end": pred.isoformat(),
                     "last_day_above_100": actual.isoformat(), "error_days": (pred - actual).days, "haze_days": len(haze)})
        print(rows[-1])
    errs = [r["error_days"] for r in rows if r["year"] < date.today().year]
    summary = {"years": rows, "mean_error_days": round(sum(errs) / len(errs), 1) if errs else None,
               "mean_abs_error_days": round(sum(abs(e) for e in errs) / len(errs), 1) if errs else None,
               "note": "Positive error: the prior expected the season to end later than the last day above 100."}
    (ROOT / "history" / "replay" / "season_backtest.json").write_text(json.dumps(summary, indent=1))
    print(summary["mean_error_days"], summary["mean_abs_error_days"])


if __name__ == "__main__":
    main()
