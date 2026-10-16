"""Replay a past haze season: build the twin's inputs as they would have been on each issue date.

Only data available on the issue date is used:
  weather   GEFS 00Z run of that day (reforecast archive to 2019, operational archive from late 2020)
  fires     NASA FIRMS standard-processing archive, the 2 days before issue
  readings  NEA 24-hour PSI at noon that day (and hourly PM2.5 where NEA's API has it)
  analogs   only past days whose following 15 days had finished before the issue date
Issue time is 08:00 UTC (4 pm Singapore), when the 00Z run would have been complete.

Usage: python pipeline/replay.py --season 2015 --start 2015-09-01 --end 2015-10-25 --every 3
Writes .work/replay/inputs/<date>.json; tools/replay_run.js turns each into a scored forecast.
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone, date

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import (WORK, ARRIVALS_H, AGE_EFOLD_H, EMIS_BINS_H, REGIONS, FIRE_GRID_DEG, WEST, EAST, SOUTH, NORTH,
                    SG_LON, SG_LAT)
import trajectories
import build_inputs as B
import fetch_history
import replay_gefs
from net import get

SGT = timezone(timedelta(hours=8))
FIRMS_SP = {2015: ["VIIRS_SNPP_SP"], 2019: ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"],
            2023: ["VIIRS_SNPP_SP", "VIIRS_NOAA20_SP", "VIIRS_NOAA21_SP"]}
BREAK = [(0, 0), (50, 12), (100, 55), (200, 150), (300, 250), (400, 350), (500, 500)]


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def pm_of_psi(psi):
    for (p0, c0), (p1, c1) in zip(BREAK, BREAK[1:]):
        if psi <= p1:
            return c0 + (psi - p0) * (c1 - c0) / (p1 - p0)
    return 500.0


def nea_block(daily, day, t0):
    """NEA-like history from the daily noon PSI, plus hourly PM2.5 from the API where it exists."""
    psi_hist, pm24_hist, pm1_hist = [], [], []
    for k in range(7, -1, -1):
        d = (day - timedelta(days=k)).isoformat()
        if d in daily:
            t = datetime.fromisoformat(d + "T12:00:00+08:00").astimezone(timezone.utc)
            psi_hist.append({"t": t.isoformat(), "national": float(daily[d])})
            pm24_hist.append({"t": t.isoformat(), "national": round(pm_of_psi(daily[d]), 1)})
    try:
        import fetch_nea
        for d in (day - timedelta(days=1), day):
            for t, rd in fetch_nea._flatten(fetch_nea._items("pm25", d.isoformat())):
                pm1 = fetch_nea._pick(rd, "pm25_one_hourly", "pm25_1h")
                if pm1 and t <= t0:
                    vals = [pm1[r] for r in ("north", "south", "east", "west", "central") if r in pm1]
                    if vals:
                        pm1_hist.append({"t": t.isoformat(), "national": max(vals)})
    except Exception as e:
        log(f"  hourly PM2.5 not available: {str(e)[:80]}")
    if not psi_hist:
        raise RuntimeError(f"no NEA PSI for {day}")
    return {"psi_hist": psi_hist, "pm24_hist": pm24_hist, "pm1_hist": pm1_hist, "latest_time": psi_hist[-1]["t"]}


def fires(t0, year):
    key = os.environ.get("FIRMS_MAP_KEY")
    area = f"{WEST:g},{SOUTH:g},{EAST:g},{NORTH:g}"
    start = (t0 - timedelta(days=2)).strftime("%Y-%m-%d")
    out, status = [], {}
    for src in FIRMS_SP.get(year, ["VIIRS_SNPP_SP"]):
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{area}/3/{start}"
        try:
            text = get(url, timeout=120, retries=3, min_interval=1.0).text
        except Exception as e:
            status[src] = "failed"
            continue
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            try:
                if (row.get("confidence") or "").strip().lower() in ("l", "low"):
                    continue
                t = datetime.strptime(row["acq_date"] + row["acq_time"].zfill(4), "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc)
                h = (t0 - t).total_seconds() / 3600
                if h < 0 or h > 48:
                    continue
                out.append({"lon": float(row["longitude"]), "lat": float(row["latitude"]), "frp": float(row.get("frp") or 0),
                            "h": h, "sat": {"VIIRS_SNPP_SP": "SNPP", "VIIRS_NOAA20_SP": "N20", "VIIRS_NOAA21_SP": "N21"}[src], "conf": "n"})
                n += 1
            except (KeyError, ValueError):
                continue
        status[src] = n
    return out, status


def build(day, season, daily, archive, members):
    t0 = datetime(day.year, day.month, day.day, 8, tzinfo=timezone.utc)
    cycle = datetime(day.year, day.month, day.day, 0, tzinfo=timezone.utc)
    nea = nea_block(daily, day, t0)
    dets, fst = fires(t0, season)
    log(f"  {day}: PSI {nea['psi_hist'][-1]['national']:.0f}, fires {fst}, PM2.5 hours {len(nea['pm1_hist'])}")
    g = replay_gefs.fetch(cycle, archive, members, log=log)
    weather = replay_gefs_to_weather(g, t0)
    fire_list = [(d["lon"], d["lat"], d["frp"] / max(1, len({x['sat'] for x in dets})), d["h"]) for d in dets]
    cells = trajectories.make_fire_cells(fire_list, FIRE_GRID_DEG, WEST, SOUTH, EAST, NORTH)
    S, paths, path_arrivals = trajectories.footprints(weather, cells, log=log)
    rain_reg, rain_t = B.region_rain(weather, cells)
    frp0 = [round(sum(c["frp0"] for c in cells["cells"] if c.get("region") == ri), 1) for ri in range(len(REGIONS))]
    frp1 = [round(sum(c["frp1"] for c in cells["cells"] if c.get("region") == ri), 1) for ri in range(len(REGIONS))]
    n0 = [sum(c["n0"] for c in cells["cells"] if c.get("region") == ri) for ri in range(len(REGIONS))]
    psi_now = nea["psi_hist"][-1]["national"]
    d2 = (day - timedelta(days=2)).isoformat()
    analogs = fetch_history.analogs(daily, psi_now, daily.get(d2), day)
    obs = B.obs_block(nea, t0)
    return {
        "version": 1, "sample": False, "replay": {"season": season, "archive": archive, "members": len(members)},
        "built": datetime.now(timezone.utc).isoformat(), "t0": t0.isoformat(), "cycle": cycle.isoformat(),
        "members": list(weather["members"]), "receptor": [SG_LON, SG_LAT], "arrivals_h": ARRIVALS_H,
        "age_efold_h": AGE_EFOLD_H, "emis_bins_h": EMIS_BINS_H,
        "regions": [{**r, "frp0": frp0[i], "frp1": frp1[i], "n0": n0[i]} for i, r in enumerate(REGIONS)],
        "S": B.quant_log16(S), "rain": {"t_h": [float(x) for x in rain_t], "data": B.b64(rain_reg, np.float32)},
        "obs": obs, "hotspots": [], "wind": B.wind_display(weather),
        "paths": {"arrivals_h": path_arrivals, "step_h": 3, "members": []},
        "summary": B.summary(S, cells, rain_reg, rain_t, weather), "climate": {}, "extras": {},
        "signals": None, "dryness": None, "analogs": analogs, "sources": [],
    }


def replay_gefs_to_weather(g, t0):
    import fetch_gefs
    return fetch_gefs.to_weather(g, t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--members", type=int, default=0, help="0 = all available")
    args = ap.parse_args()
    archive = "reforecast" if args.season <= 2019 else "operational"
    members = ["c00", "p01", "p02", "p03", "p04"] if archive == "reforecast" else (["c00"] + [f"p{i:02d}" for i in range(1, 31)])
    if args.members:
        members = members[:args.members]
    daily = json.loads((WORK.parent / "history" / "psi_daily.json").read_text())["daily"]
    outdir = WORK / "replay" / "inputs"
    outdir.mkdir(parents=True, exist_ok=True)
    day = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    ok = 0
    while day <= end:
        path = outdir / f"{day.isoformat()}.json"
        if not path.exists():
            t = time.time()
            try:
                inp = build(day, args.season, daily, archive, members)
                path.write_text(json.dumps(inp, separators=(",", ":")))
                ok += 1
                log(f"  wrote {path.name} ({path.stat().st_size / 1e6:.1f} MB) in {time.time() - t:.0f}s")
            except Exception:
                log(f"  {day} failed:")
                traceback.print_exc()
        day += timedelta(days=args.every)
    log(f"done: {ok} issue dates built")


if __name__ == "__main__":
    main()
