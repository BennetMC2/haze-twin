"""The page's short written column: what the model expects, in a few plain sentences.

Written by Claude (claude-opus-5-5) from the latest forecast numbers only, and checked so that every number in
the text appears in the facts it was given. Writes site/data/column.json. If the API key is missing or the call
fails, nothing is written and the page falls back to its own template text.
Usage: python tools/column.py   (needs ANTHROPIC_API_KEY)
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "site" / "data"
MODEL = "claude-opus-5-5"
SGT = timezone(timedelta(hours=8))

SYSTEM = """You write a short daily haze column for a Singapore website that forecasts the 24-hour PSI.
Write 3 or 4 sentences, about 70 words, in plain British English, like a good newspaper weather column.
Say what is happening to the air now and why (winds, which fires, rain), which day looks worst or best, and the one thing to watch.
Use only the facts given. Every number you use must appear in the facts. Round nothing new.
No health advice, no exclamation marks, no marketing words, no headings, no bullet points, no greeting, no sign-off.
Do not repeat the date or time; it is shown separately. Do not call the model "we"; say "the model" or just state the forecast."""


def facts():
    fc = json.loads((DATA / "forecast.json").read_text())
    lite = json.loads((DATA / "inputs_lite.json").read_text())
    n = fc["regions"]["national"]
    days = fc["days"]
    L = []
    obs_t = datetime.fromisoformat((fc.get("obsTime") or lite["obs"]["time"]).replace("Z", "+00:00")).astimezone(SGT)
    L.append(f"NEA 24-hour PSI now: {round(n['now'])} (reading at {obs_t:%H:%M} SGT, {obs_t:%a %d %b}).")
    L.append("Storylines for the next 6 days (share of 100,000 simulated futures):")
    for c in sorted([c for c in n["classes"] if not c.get("hidden")], key=lambda c: -c["odds"]):
        L.append(f"- {c['name']}: {round(c['odds'])}%. {c.get('outcome', '')}")
    L.append("Best-guess PSI by day: " + ", ".join(f"{days[i]['label']} {d['p50']}" for i, d in enumerate(n["days"][:8])))
    L.append(f"Chance PSI is 100 or below by {n.get('clearDayLabel') or days[min(5, len(days) - 1)]['label']}: {round(n.get('clear', 0))}%.")
    se = fc.get("season") or {}
    if se.get("median"):
        L.append(f"Haze season end (rough, from when the rains usually arrive): around {se['median']}.")
    s = lite.get("summary") or {}
    if s.get("transport_share_0_72h"):
        L.append("Where Singapore's air passes over fires, next 3 days: " + ", ".join(f"{k} {round(v * 100)}%" for k, v in s["transport_share_0_72h"].items() if v >= 0.05))
    if s.get("rain_mm_0_120h"):
        L.append("Rain expected over the fire areas, next 5 days (mm): " + ", ".join(f"{k} {round(v)}" for k, v in s["rain_mm_0_120h"].items() if k != "Malaysia"))
    if s.get("frp24_mw"):
        L.append("Fire power seen by satellite in the last day (MW): " + ", ".join(f"{k} {round(v)}" for k, v in s["frp24_mw"].items() if v >= 100))
    regs = [r for r in (fc.get("sourceRegions") or []) if r.get("share", 0) >= 10]
    if regs:
        L.append("Main smoke sources for Singapore, next 3 days: " + ", ".join(f"{r['name']} {round(r['share'])}%" for r in regs))
    return fc, "\n".join(L)


def bad_numbers(text, fact_text):
    """Numbers in the text that are not in the facts. Small numbers (day counts, dates) are allowed."""
    allowed = set(re.findall(r"\d+", fact_text))
    return [x for x in re.findall(r"\d+", text) if x not in allowed and int(x) > 31]


def main():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("column: no ANTHROPIC_API_KEY, skipped")
        return
    fc, f = facts()
    note = ""
    for attempt in range(3):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 400, "system": SYSTEM, "messages": [{"role": "user", "content": "Facts:\n" + f + note}]})
            r.raise_for_status()
            text = " ".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        except Exception as e:
            print(f"column: request failed ({type(e).__name__})")
            continue
        bad = bad_numbers(text, f) if text else []
        if text and not bad and len(text) < 900:
            (DATA / "column.json").write_text(json.dumps({"t0": fc.get("t0"), "issued": fc.get("issued"), "model": MODEL, "text": text}))
            print("column written:", len(text), "chars")
            return
        print("column: rejected", ("numbers not in the facts: " + ", ".join(bad)) if bad else "(empty or too long)")
        note = "\n\nYour last draft used numbers that are not in the facts (" + ", ".join(bad) + "). Use only numbers from the facts." if bad else ""
    print("column: not written; the page uses its own text")


if __name__ == "__main__":
    main()
