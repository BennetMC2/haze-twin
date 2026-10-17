"""Plain-text copies of the latest forecast for search engines, AI assistants and people without JavaScript.

Writes, from site/data/forecast.json:
  - the "Forecast in words" section and FAQ inside site/index.html (between STATIC markers), plus FAQ JSON-LD
  - site/latest.md   the forecast as Markdown (linked from llms.txt)
  - site/llms.txt    what the site is and where the latest forecast lives, for AI assistants
  - site/robots.txt, site/sitemap.xml
Run after engine/run.js in the workflows. SITE_URL (env) is the public address.
Usage: python tools/build_static.py
"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
URL = (os.environ.get("SITE_URL") or "https://bennetmc2.github.io/haze-twin/").rstrip("/") + "/"
NAME = "Singapore Haze Prediction Model"
SGT = timezone(timedelta(hours=8))
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def day_label(iso):
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SGT)
    return f"{DOW[d.weekday()]} {d.day} {MON[d.month - 1]}"


def when(iso):
    d = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SGT)
    h = d.hour % 12 or 12
    return f"{DOW[d.weekday()]} {d.day} {MON[d.month - 1]}, {h}{':%02d' % d.minute if d.minute else ''}{'am' if d.hour < 12 else 'pm'} SGT"


def pct(v):
    return "under 1%" if v < 1 else ("over 99%" if v > 99 else f"{round(v)}%")


def build():
    fc = json.loads((SITE / "data" / "forecast.json").read_text())
    r = fc["regions"]["national"]
    days = fc["days"]
    stories = [c for c in r["classes"] if not c.get("hidden") and c.get("odds", 0) >= 1]
    stories.sort(key=lambda c: -c["odds"])
    issued = fc.get("issued") or fc.get("built")
    se = fc.get("season") or {}
    nC = min(6, len(days))
    clear_by = r.get("clearBy") or []
    i50 = next((i for i, v in enumerate(clear_by) if round(v) >= 50), None)
    over = [v for v in (r.get("overBy") or []) if v is not None]

    lines = []
    lines.append(f"PSI now: {round(r['now'])} (NEA 24-hour PSI, highest of Singapore's five areas).")
    if stories:
        lines.append("Most likely next 6 days: " + "; ".join(f"{c['name'].lower()} ({pct(c['odds'])}): {c.get('outcome', '')}" for c in stories[:2]))
    if r.get("mode") == "haze":
        lines.append(f"Back below 100 (first day at 100 or below): {pct(r.get('clear', 0))} chance by {day_label(days[nC - 1]['iso'])}"
                     + (f"; even odds by {day_label(days[i50]['iso'])}." if i50 is not None else "; less than even odds within 15 days."))
        if over:
            lines.append(f"Haze over (7 days in a row at 100 or below): {pct(over[-1])} chance it has started by {day_label(days[len(over) - 1]['iso'])}.")
    if se.get("median"):
        lines.append(f"End of the haze season (rain-season outlook, experimental): most likely around {se['median']} (80% between {se.get('p10')} and {se.get('p90')}).")

    rows = []
    for i, d in enumerate(r["days"]):
        b = d["bands"]
        rows.append((day_label(days[i]["iso"]), d["p50"], f"{d.get('p25', d['p5'])}–{d.get('p75', d['p95'])}", pct(b[2] + b[3] + b[4]), pct(b[3] + b[4]), pct(b[4])))

    faq = [
        ("When will the haze in Singapore end?",
         (f"The model gives a {pct(r.get('clear', 0))} chance the 24-hour PSI is back to 100 or below by {day_label(days[nC - 1]['iso'])}. " if r.get("mode") == "haze" else "The 24-hour PSI is currently 100 or below. ")
         + (f"The haze season most likely ends around {se['median']}, when steady rain sets in over the fire areas (experimental estimate)." if se.get("median") else "")),
        ("Will the haze get worse this week?",
         " ".join(f"{c['name']}: {pct(c['odds'])} chance. {c.get('outcome', '')}" for c in stories[:3])),
        ("Is this NEA's official haze forecast?",
         "No. This is an independent, experimental model. For official readings and health advice, follow NEA at haze.gov.sg."),
        ("How does the Singapore Haze Prediction Model work?",
         "It traces the air arriving in Singapore back through up to 56 weather forecasts (NOAA GEFS and ECMWF's AI model), adds smoke from fires seen by NASA satellites, "
         "and simulates 100,000 possible next 15 days, weighted to match NEA's latest readings. The odds rerun every hour; the full model runs every 6 hours."),
        ("How accurate is it?",
         "In replays of the 2014, 2015, 2019 and 2023 haze seasons, about 85% of NEA readings fell inside the forecast's 90% range. For 1 to 6 days ahead the best guess "
         "was about as good as assuming PSI stays as it is today; for 7 to 15 days ahead it was clearly better. Every live forecast is scored daily on the Track record page."),
    ]

    esc = html.escape
    sec = [f'<section class="panel inwords" id="inWords" aria-labelledby="iwTitle"><h2 id="iwTitle">Singapore haze forecast in words</h2>',
           f'<p class="note">Issued {esc(when(issued))}. Independent, experimental model; not an official NEA forecast.</p>',
           "<ul>" + "".join(f"<li>{esc(l)}</li>" for l in lines) + "</ul>",
           '<details class="more"><summary>Day by day, as a table</summary><div class="chart-wrap"><table class="ltab"><thead><tr><th>Day</th><th>Best guess PSI</th><th>Likely range</th><th>Above 100</th><th>Above 200</th><th>Above 300</th></tr></thead><tbody>'
           + "".join(f"<tr><td>{esc(a)}</td><td>{b}</td><td>{esc(c)}</td><td>{d}</td><td>{e}</td><td>{f}</td></tr>" for a, b, c, d, e, f in rows)
           + "</tbody></table></div></details>",
           '<h2>Questions people ask</h2>' + "".join(f"<details class=\"more\"><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in faq),
           "</section>"]
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebApplication", "name": NAME, "url": URL, "applicationCategory": "WeatherApplication", "operatingSystem": "Any",
         "description": "Odds for Singapore's haze (24-hour PSI) over the next 15 days, when it will end, and where the smoke comes from. Independent model, updated hourly.",
         "offers": {"@type": "Offer", "price": "0", "priceCurrency": "SGD"}},
        {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}]}

    idx = (SITE / "index.html").read_text()
    block = "<!--STATIC-START-->" + "".join(sec) + "<!--STATIC-END-->"
    idx = re.sub(r"<!--STATIC-START-->.*?<!--STATIC-END-->", lambda m: block, idx, flags=re.S)
    ldtag = '<script type="application/ld+json" id="ld">' + json.dumps(ld).replace("</", "<\\/") + "</script>"
    idx = re.sub(r'<script type="application/ld\+json" id="ld">.*?</script>', lambda m: ldtag, idx, flags=re.S)
    idx = re.sub(r'<link rel="canonical" href="[^"]*">', f'<link rel="canonical" href="{URL}">', idx)
    idx = idx.replace('content="https://bennetmc2.github.io/haze-twin/"', f'content="{URL}"').replace("https://bennetmc2.github.io/haze-twin/data/og.png", URL + "data/og.png")
    (SITE / "index.html").write_text(idx)

    md = [f"# {NAME}: latest forecast", "", f"Issued {when(issued)}. Independent, experimental model; not an official NEA forecast. Source: {URL}", ""]
    md += [f"- {l}" for l in lines] + ["", "| Day | Best guess PSI | Likely range | Above 100 | Above 200 | Above 300 |", "|---|---|---|---|---|---|"]
    md += [f"| {a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in rows]
    md += ["", "## Questions", ""] + [f"**{q}** {a}\n" for q, a in faq]
    (SITE / "latest.md").write_text("\n".join(md))
    (SITE / "llms.txt").write_text("\n".join([
        f"# {NAME}", "",
        "> Odds for Singapore's haze over the next 15 days: the chance the 24-hour PSI is above 100, 200 or 300 each day, when the haze is likely to end, "
        "and which fires the smoke comes from. Independent and experimental, not NEA's official forecast. Odds rerun hourly; the full model runs every 6 hours.", "",
        "## Latest", f"- [Latest forecast in Markdown]({URL}latest.md): updated every hour",
        f"- [Forecast data (JSON)]({URL}data/forecast.json)", "",
        "## About", f"- [The site]({URL})", f"- [Source code and method](https://github.com/BennetMC2/haze-twin)", "",
        "When quoting, say the numbers are odds from an independent model, give the issue time, and point to NEA (haze.gov.sg) for official readings and health advice."]))
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {URL}sitemap.xml\n")
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{URL}</loc><lastmod>{datetime.now(timezone.utc).date().isoformat()}</lastmod><changefreq>hourly</changefreq></url>"
        f"<url><loc>{URL}latest.md</loc><changefreq>hourly</changefreq></url></urlset>\n")
    print("static copies written for", URL)


if __name__ == "__main__":
    build()
