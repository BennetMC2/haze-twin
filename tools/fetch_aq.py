"""Save the latest AQICN (World Air Quality Index) reading for Singapore Central to site/data/aq.json.
The token stays in the WAQI_TOKEN repository secret; the page only reads the saved file.
Usage: python tools/fetch_aq.py site/data/aq.json"""
import json
import os
import sys

import requests


def main(out):
    token = os.environ.get("WAQI_TOKEN", "").strip()
    if not token:
        print("WAQI_TOKEN not set; skipping AQICN reading")
        return
    try:
        js = requests.get("https://api.waqi.info/feed/singapore/central/", params={"token": token}, timeout=20).json()
    except Exception as e:
        print("AQICN request failed:", str(e)[:120])
        return
    d = js.get("data") if isinstance(js, dict) else None
    if js.get("status") != "ok" or not isinstance(d, dict) or not isinstance(d.get("aqi"), (int, float)):
        print("AQICN gave no reading:", str(js)[:160].replace(token, "TOKEN"))
        return
    iaqi = d.get("iaqi") or {}
    rec = {"aqi": d["aqi"], "time": (d.get("time") or {}).get("iso"), "station": (d.get("city") or {}).get("name"),
           "url": (d.get("city") or {}).get("url"), "pm25": (iaqi.get("pm25") or {}).get("v")}
    with open(out, "w") as f:
        json.dump(rec, f)
    print("AQICN:", rec)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "site/data/aq.json")
