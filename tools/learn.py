"""Daily learning step: score the forecasts against what NEA measured, find out why they missed,
and make a small, capped correction for the next forecasts.

1. Score: every forecast issued in the last 15 days against NEA's 24-hour PSI at noon, by lead
   (days 1-3, 4-6, 7-15): bias (did we run high or low), how often the reading fell inside the
   possible range, and the typical miss against "same as today" (persistence).
2. Diagnose: Claude Opus 5.5 reads the scores and what the fires, rain and winds did, and writes a
   short note on why the forecasts missed. (Skipped if ANTHROPIC_API_KEY is not set.)
3. Adjust: the code (not the AI) nudges a bias and spread correction per lead group, in small
   capped steps, only when there are at least 3 scored days. Opus's suggestion is recorded and
   counted as agreeing or not.

Writes history/learn/params.json (read by engine/run.js) and history/learn/log.json (shown on the
Track record page). Usage: python tools/learn.py
"""
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
HIST = ROOT / "history"
OUT = HIST / "learn"
SGT = timezone(timedelta(hours=8))
GROUPS = [("Days 1-3", 12, 84), ("Days 4-6", 84, 156), ("Days 7-15", 156, 372)]
MODEL = "claude-opus-5-5"
STEP_CAP = 0.08          # most the bias can move in one day (log units, about 8%)
BIAS_CAP = 0.4           # total bias cap (about x1.5 either way)
SPREAD_LIMITS = (0.7, 1.5)
MIN_DAYS = 3


def load(p, d):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return d


def score(runs, obs, today, cur):
    """-> per-group stats over the last 15 days, plus the latest scored day in detail."""
    groups = [{"name": g[0], "pairs": []} for g in GROUPS]
    days = sorted(k for k in obs if k[:10] >= (today - timedelta(days=15)).isoformat())
    for k in days:
        o = obs[k].get("national")
        if o is None:
            continue
        target = datetime.fromisoformat(k + ":00:00+00:00")
        for r in runs:
            if r.get("sample"):
                continue
            lead = (target - datetime.fromisoformat(r["t0"].replace("Z", "+00:00"))).total_seconds() / 3600
            reg = (r.get("regions") or {}).get("national")
            if not reg:
                continue
            d = next((x for x in reg["days"] if x["iso"][:13] == k), None)
            if not d:
                continue
            for gi, (_, lo, hi) in enumerate(GROUPS):
                if lo < lead <= hi:
                    was = ((r.get("learn") or {}).get("bias") or [0, 0, 0])[gi]
                    adj = math.exp(cur["bias"][gi] - was)   # what this forecast would have said with today's correction
                    wsp = ((r.get("learn") or {}).get("spread") or [1, 1, 1])[gi]
                    ksp = cur["spread"][gi] / (wsp or 1)
                    groups[gi]["pairs"].append({"day": k[:10], "lead_h": round(lead), "issued": r["t0"], "p5": d["p5"], "p50": d["p50"],
                                                "p95": d["p95"], "obs": o, "persist": reg.get("now"), "adj": adj, "ksp": ksp})
    out = []
    for g in groups:
        P = g["pairs"]
        ndays = len({p["day"] for p in P})
        if not P:
            out.append({"name": g["name"], "n": 0, "days": 0})
            continue
        # weight each scored day equally, however many hourly runs covered it
        byday = {}
        for p in P:
            byday.setdefault(p["day"], []).append(p)
        def mean_over_days(fn):
            return sum(sum(fn(p) for p in v) / len(v) for v in byday.values()) / len(byday)
        e = mean_over_days(lambda p: math.log(max(1, p["obs"]) / max(1, p["p50"])))
        def inside(p):   # possible range as it would be with today's corrections
            m = math.log(max(1, p["p50"] * p["adj"]))
            lo = math.exp(m + p["ksp"] * (math.log(max(1, p["p5"] * p["adj"])) - m))
            hi = math.exp(m + p["ksp"] * (math.log(max(1, p["p95"] * p["adj"])) - m))
            return 1.0 if lo <= p["obs"] <= hi else 0.0
        cov = mean_over_days(inside)
        mae = mean_over_days(lambda p: abs(p["p50"] - p["obs"]))
        maep = mean_over_days(lambda p: abs((p["persist"] or p["obs"]) - p["obs"]))
        recent = sorted(byday)[-7:]
        e7 = sum(sum(math.log(max(1, p["obs"]) / max(1, p["p50"] * p["adj"])) for p in byday[d]) / len(byday[d]) for d in recent) / len(recent)
        out.append({"name": g["name"], "n": len(P), "days": ndays, "bias_log": round(e, 3), "bias_log_7d": round(e7, 3), "bias_left_pct": round((math.exp(e7) - 1) * 100),
                    "bias_pct": round((math.exp(e) - 1) * 100), "coverage90": round(cov * 100), "mae": round(mae, 1),
                    "mae_persistence": round(maep, 1)})
    latest = max((p["day"] for g in groups for p in g["pairs"]), default=None)
    detail = []
    if latest:
        for gi, g in enumerate(groups):
            ps = [p for p in g["pairs"] if p["day"] == latest]
            if ps:
                mid = (GROUPS[gi][1] + GROUPS[gi][2]) / 2
                p = min(ps, key=lambda x: abs(x["lead_h"] - mid))
                detail.append({"group": g["name"], **p})
    return out, latest, detail


def conditions(runs, latest):
    """What the model expected about fires, rain and transport when it issued, against the latest run near the day."""
    if not latest:
        return None
    real = [r for r in runs if not r.get("sample") and r.get("summary")]
    if not real:
        return None
    target = datetime.fromisoformat(latest + "T04:00:00+00:00")
    def near(lead_h):
        c = [r for r in real if 0 <= (target - datetime.fromisoformat(r["t0"].replace("Z", "+00:00"))).total_seconds() / 3600 <= lead_h + 12]
        c.sort(key=lambda r: abs((target - datetime.fromisoformat(r["t0"].replace("Z", "+00:00"))).total_seconds() / 3600 - lead_h))
        return c[0] if c else None
    then, now = near(48), near(0)
    if not then or not now:
        return None
    return {"issued_48h_before": {"t0": then["t0"], **then["summary"]}, "on_the_day": {"t0": now["t0"], **now["summary"]}}


def ask_opus(payload):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None, "ANTHROPIC_API_KEY not set"
    system = ("You review a haze forecast for Singapore every day. You get scores of recent forecasts against NEA's measured "
              "24-hour PSI, and what the fires (FRP, MW), rain over the fire areas (mm, next 5 days) and air transport "
              "(share of air reaching Singapore from each area) looked like when a forecast was issued and on the day. "
              "Write a short note for the public: 3 to 5 plain sentences, no jargon, no markdown. Say how the forecasts did, "
              "the most likely reason for the biggest miss, using only the numbers given, and what that suggests. "
              "If the data is too thin to say, say so. Then on a new line output JSON exactly like "
              '{"bias": {"Days 1-3": "up|down|keep", "Days 4-6": "up|down|keep", "Days 7-15": "up|down|keep"}, '
              '"spread": {"Days 1-3": "wider|narrower|keep", "Days 4-6": "wider|narrower|keep", "Days 7-15": "wider|narrower|keep"}} '
              'where bias "up" means forecasts should be higher.')
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
            "content-type": "application/json", "x-api-key": key, "anthropic-version": "2023-06-01"},
            json={"model": MODEL, "max_tokens": 700, "system": system,
                  "messages": [{"role": "user", "content": json.dumps(payload)}]})
        if r.status_code != 200:
            return None, f"API {r.status_code}: {r.text[:160]}"
        text = "".join(c.get("text", "") for c in r.json().get("content", [])).strip()
    except Exception as e:
        return None, str(e)[:160]
    m = re.search(r"\{[\s\S]*\}\s*$", text)
    sugg = None
    if m:
        try:
            sugg = json.loads(m.group(0))
            text = text[:m.start()].strip()
        except Exception:
            sugg = None
    return {"note": text, "suggest": sugg, "model": MODEL}, None


def main():
    now = datetime.now(timezone.utc)
    today = now.astimezone(SGT).date()
    runs = load(HIST / "runs.json", [])
    obs = load(HIST / "obs.json", {})
    params = load(OUT / "params.json", {"bias": [0, 0, 0], "spread": [1, 1, 1]})
    log = load(OUT / "log.json", [])
    stats, latest, detail = score(runs, obs, today, params)
    cond = conditions(runs, latest)

    # the rule-based step: move half way toward correcting what is still left over after today's correction, capped
    new_bias, new_spread, changes = list(params["bias"]), list(params["spread"]), []
    for i, g in enumerate(stats):
        if g.get("days", 0) < MIN_DAYS:
            changes.append({"group": g["name"], "what": "no change", "why": f"only {g.get('days', 0)} scored days so far; waiting for {MIN_DAYS}"})
            continue
        step = max(-STEP_CAP, min(STEP_CAP, 0.5 * g["bias_log_7d"]))
        if abs(step) >= 0.01:
            new_bias[i] = round(max(-BIAS_CAP, min(BIAS_CAP, params["bias"][i] + step)), 3)
            changes.append({"group": g["name"], "what": f"forecasts {'up' if step > 0 else 'down'} about {abs(round((math.exp(step) - 1) * 100))}%",
                            "why": f"over the last week, readings ran {abs(g['bias_left_pct'])}% {'above' if g['bias_left_pct'] > 0 else 'below'} the best guess, after the corrections already made"})
        sp = params["spread"][i]
        if g["coverage90"] < 75:
            new_spread[i] = round(min(SPREAD_LIMITS[1], sp * 1.08), 3)
            changes.append({"group": g["name"], "what": "range wider by 8%", "why": f"only {g['coverage90']}% of readings fell inside the possible range (aim: 90%)"})
        elif g["coverage90"] > 97 and g["days"] >= 7:
            new_spread[i] = round(max(SPREAD_LIMITS[0], sp * 0.95), 3)
            changes.append({"group": g["name"], "what": "range narrower by 5%", "why": f"{g['coverage90']}% of readings fell inside the possible range (aim: 90%)"})

    payload = {"today": today.isoformat(), "scores": stats, "latest_day": latest, "latest_detail": detail,
               "conditions": cond, "current_corrections": params, "planned_changes": changes}
    review, err = ask_opus(payload)
    agree = None
    if review and review.get("suggest"):
        sb = review["suggest"].get("bias", {})
        dirs = []
        for i, g in enumerate(stats):
            want = sb.get(g["name"])
            moved = new_bias[i] - params["bias"][i]
            got = "up" if moved > 0.005 else "down" if moved < -0.005 else "keep"
            if want:
                dirs.append(want == got)
        agree = (sum(dirs), len(dirs)) if dirs else None

    entry = {"date": today.isoformat(), "at": now.isoformat(), "scored_day": latest, "scores": stats, "latest": detail,
             "conditions": cond, "changes": changes, "before": params,
             "after": {"bias": new_bias, "spread": new_spread},
             "review": review, "review_error": err, "agree": agree}
    log = [e for e in log if e.get("date") != entry["date"]] + [entry]
    log = log[-120:]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "params.json").write_text(json.dumps({"date": today.isoformat(), "bias": new_bias, "spread": new_spread}))
    (OUT / "log.json").write_text(json.dumps(log))
    print(json.dumps({k: entry[k] for k in ("date", "scored_day", "changes", "after", "agree", "review_error")}, indent=1))
    if review:
        print("note:", review["note"])


if __name__ == "__main__":
    main()
