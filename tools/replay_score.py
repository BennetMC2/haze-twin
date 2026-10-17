"""Score replayed forecasts against what NEA measured (24-hour PSI at noon), and against simple baselines.

Baselines:
  persistence   tomorrow and every later day = the PSI on the issue date
  climatology   the spread of all past noon PSI readings on that calendar window (Aug-Nov), years before the season
Writes history/replay/summary.json
"""
import json
import re
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SGT = timezone(timedelta(hours=8))
daily = json.loads((ROOT / "history" / "psi_daily.json").read_text())["daily"]
REPLAY = ROOT / "history" / "replay"


def band(v):
    return 0 if v <= 50 else 1 if v <= 100 else 2 if v <= 200 else 3 if v <= 300 else 4


def interp_q(qs, p):
    """Quantile function from the stored quantiles (5, 25, 50, 75, 95), linear in between."""
    xs = [0.05, 0.25, 0.5, 0.75, 0.95]
    if p <= xs[0]:
        return qs[0]
    if p >= xs[-1]:
        return qs[-1]
    for i in range(4):
        if p <= xs[i + 1]:
            w = (p - xs[i]) / (xs[i + 1] - xs[i])
            return qs[i] + w * (qs[i + 1] - qs[i])


def crps_q(qs, y):
    """CRPS from quantiles (average pinball loss over 19 levels, times 2)."""
    tot = 0
    levels = [i / 20 for i in range(1, 20)]
    for p in levels:
        q = interp_q(qs, p)
        tot += (p - (y < q)) * (y - q)
    return 2 * tot / len(levels)


# the one calibration, read from engine.js so scoring and production cannot drift apart.
# The stored replay forecasts (history/replay/*.json) are raw: they were made before calibration existed,
# so this is applied exactly once. Like the engine: log-PSI space around p50, capped at 500.
_ENG = (Path(__file__).resolve().parents[1] / "engine" / "engine.js").read_text()
CALIB_LO = json.loads(re.search(r"const CALIB_LO = (\[[^\]]*\])", _ENG).group(1))
CALIB_HI = json.loads(re.search(r"const CALIB_HI = (\[[^\]]*\])", _ENG).group(1))


def widen(qs, lead):
    i = 0 if lead <= 3 else 1 if lead <= 6 else 2
    m = math.log(max(1, qs[2]))
    return [min(500.0, math.exp(m + (CALIB_LO[i] if math.log(max(1, q)) < m else CALIB_HI[i]) * (math.log(max(1, q)) - m))) for q in qs]


def score(season):
    fc = json.loads((REPLAY / f"{season}.json").read_text())
    rows = []
    for issue, f in sorted(fc.items()):
        d0 = date.fromisoformat(issue)
        persist = daily.get(issue)
        if persist is None:
            continue
        clim = sorted(v for k, v in daily.items() if int(k[:4]) < season and k[5:7] in ("08", "09", "10", "11"))
        for k, d in enumerate(f["days"]):
            vd = datetime.fromisoformat(d["iso"].replace("Z", "+00:00")).astimezone(SGT).date()
            lead = (vd - d0).days
            obs = daily.get(vd.isoformat())
            if obs is None or lead < 1:
                continue
            qs = [d["p5"], d.get("p25", d["p5"]), d["p50"], d.get("p75", d["p95"]), d["p95"]]
            p100 = (d["bands"][2] + d["bands"][3] + d["bands"][4]) / 100
            p200 = (d["bands"][3] + d["bands"][4]) / 100
            pband = d["bands"][band(obs)] / 100
            n = len(clim)
            cq = [clim[int(n * x)] for x in (0.05, 0.25, 0.5, 0.75, 0.95)]
            cq2 = widen(qs, lead)
            rows.append({
                "in90c": cq2[0] <= obs <= cq2[4], "in50c": cq2[1] <= obs <= cq2[3], "crpsc": crps_q(cq2, obs),
                "issue": issue, "lead": lead, "obs": obs, "p5": qs[0], "p25": qs[1], "p50": qs[2], "p75": qs[3], "p95": qs[4],
                "in90": qs[0] <= obs <= qs[4], "in50": qs[1] <= obs <= qs[3],
                "ae": abs(qs[2] - obs), "ae_persist": abs(persist - obs),
                "crps": crps_q(qs, obs), "crps_persist": abs(persist - obs), "crps_clim": crps_q(cq, obs),
                "brier100": (p100 - (obs > 100)) ** 2, "brier100_persist": ((persist > 100) - (obs > 100)) ** 2,
                "brier200": (p200 - (obs > 200)) ** 2, "brier200_persist": ((persist > 200) - (obs > 200)) ** 2,
                "band_hit": band(qs[2]) == band(obs), "band_hit_persist": band(persist) == band(obs), "p_band": pband,
                "tier": d["tier"],
            })
    return rows


def summarise(rows):
    def agg(rs):
        if not rs:
            return None
        n = len(rs)
        m = lambda k: sum(r[k] for r in rs) / n
        skill = lambda a, b: round(1 - m(a) / m(b), 2) if m(b) > 0 else None
        return {"n": n, "coverage90": round(m("in90") * 100), "coverage50": round(m("in50") * 100),
                "coverage90_calibrated": round(m("in90c") * 100), "coverage50_calibrated": round(m("in50c") * 100),
                "crps_calibrated": round(m("crpsc"), 1), "crps_skill_vs_persist_calibrated": skill("crpsc", "crps_persist"),
                "mae": round(m("ae"), 1), "mae_persist": round(m("ae_persist"), 1),
                "crps": round(m("crps"), 1), "crps_skill_vs_persist": skill("crps", "crps_persist"),
                "crps_skill_vs_clim": skill("crps", "crps_clim"),
                "brier100": round(m("brier100"), 3), "brier100_skill_vs_persist": skill("brier100", "brier100_persist"),
                "brier200": round(m("brier200"), 3), "brier200_skill_vs_persist": skill("brier200", "brier200_persist"),
                "band_hit": round(m("band_hit") * 100), "band_hit_persist": round(m("band_hit_persist") * 100)}
    by_lead = {}
    for L in range(1, 16):
        a = agg([r for r in rows if r["lead"] == L])
        if a:
            by_lead[L] = a
    return {"all_1_6": agg([r for r in rows if 1 <= r["lead"] <= 6]), "all_7_15": agg([r for r in rows if r["lead"] >= 7]),
            "by_lead": by_lead}


def main():
    out = {"built": datetime.now(timezone.utc).isoformat(), "seasons": {}}
    allrows = []
    for f in sorted(REPLAY.glob("[0-9][0-9][0-9][0-9].json")):
        season = int(f.stem)
        rows = score(season)
        allrows += rows
        # series for charts: lead-1 and lead-3 forecasts vs observed
        series = {}
        for L in (1, 3, 6):
            series[L] = [{k: r[k] for k in ("issue", "obs", "p5", "p25", "p50", "p75", "p95")} | {"valid": (date.fromisoformat(r["issue"]) + timedelta(days=L)).isoformat()}
                         for r in rows if r["lead"] == L]
        issues = sorted({r["issue"] for r in rows})
        obs = {k: v for k, v in daily.items() if issues and issues[0] <= k <= (date.fromisoformat(issues[-1]) + timedelta(days=15)).isoformat()}
        out["seasons"][season] = {"summary": summarise(rows), "series": series, "observed": obs, "issues": len(issues)}
    out["overall"] = summarise(allrows)
    (REPLAY / "summary.json").write_text(json.dumps(out, indent=1))
    o = out["overall"]["all_1_6"]
    print(json.dumps({s: v["summary"]["all_1_6"] for s, v in out["seasons"].items()}, indent=1))
    print("overall days 1-6:", o)


if __name__ == "__main__":
    main()
