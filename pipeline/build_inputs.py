"""Fetch everything, back-trace from Singapore and write site/data/inputs.json.

Usage:
  python pipeline/build_inputs.py            # live data (needs network + FIRMS_MAP_KEY)
  python pipeline/build_inputs.py --sample   # synthetic data, no network
"""
import argparse
import base64
import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__file__))
from config import (SITE_DATA, WORK, ARRIVALS_H, AGE_EFOLD_H, EMIS_BINS_H, REGIONS, FIRE_GRID_DEG, WEST, EAST,
                    SOUTH, NORTH, WIND_DISPLAY_DEG, WIND_SCALE, SG_LON, SG_LAT)
import trajectories


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


def b64(arr, dtype):
    a = np.ascontiguousarray(arr, dtype=dtype)
    return {"dtype": np.dtype(dtype).name, "shape": list(a.shape), "b64": base64.b64encode(a.tobytes()).decode()}


def quant_log16(S):
    """Store sensitivities as log-scaled uint16 (about 4 significant digits, half the size)."""
    smax = float(S.max()) if S.size else 1.0
    if not smax > 0:
        smax = 1.0           # no fire air reaches the receptor at all: store zeros
    s0 = max(smax * 1e-6, 1e-9)
    q = np.round(np.log1p(np.maximum(S, 0) / s0) / np.log1p(smax / s0) * 65535).astype(np.uint16)
    out = b64(q, np.uint16)
    out.update({"dtype": "uint16log", "s0": s0, "smax": smax})
    return out


def round_hour(t):
    return t.replace(minute=0, second=0, microsecond=0)


_PSI_BP = [(0, 0), (12, 50), (55, 100), (150, 200), (250, 300), (350, 400), (500, 500)]


def BREAK_PSI(pm):
    """PSI sub-index for 24-hour PM2.5 (same breakpoints as engine.js)."""
    if pm is None or pm <= 0:
        return 0.0
    for (c0, s0), (c1, s1) in zip(_PSI_BP, _PSI_BP[1:]):
        if pm <= c1:
            return s0 + (pm - c0) * (s1 - s0) / (c1 - c0)
    return 500.0


def city_cams(c, log=print):
    """CAMS PM2.5 forecast for a city: the second opinion a city leans on while it has no track record."""
    try:
        import fetch_extras
        return {"cams": fetch_extras.cams(log=log, lat=c["lat"], lon=c["lon"], name=c["name"])}
    except Exception as e:
        log(f"  CAMS for {c['name']} failed: {e}")
        return {}


def build_cities(base, weather, cells, t0, log=print):
    """Home cities: trace the same weather (GEFS members only) back from each city and write
    site/data/cities/<id>/inputs.json. A city without a reading is skipped (no forecast is better than an
    unanchored one)."""
    import fetch_city
    from config import CITIES
    keep = [i for i, m in enumerate(weather["members"]) if not str(m).startswith(("a", "e"))]
    w = dict(weather)
    for k in ("u", "v", "rain"):
        w[k] = weather[k][keep]
    w["members"] = [weather["members"][i] for i in keep]
    rain_reg, rain_t = region_rain(w, cells)
    done = []
    for c in CITIES:
        r = fetch_city.reading(c, log=log)
        if not r or r.get("pm25") is None:
            continue
        log(f"back-tracing from {c['name']} ({len(keep)} forecasts); reading PM2.5 {r['pm25']} at {r.get('station')}")
        S, paths, path_arrivals = trajectories.footprints(w, cells, log=None, receptor=(c["lon"], c["lat"]))
        out = dict(base)
        out.update({
            "city": {k: c[k] for k in ("id", "name", "country", "lon", "lat")},
            "members": list(w["members"]), "receptor": [c["lon"], c["lat"]],
            "S": quant_log16(S), "rain": {"t_h": [float(x) for x in rain_t], "data": b64(rain_reg, np.float32)},
            "obs": fetch_city.obs_block(r, t0),
            "paths": {"arrivals_h": path_arrivals, "step_h": 3,
                      "members": [[[[round(x, 2), round(y, 2)] for x, y in p] for p in mem] for mem in paths]},
            "summary": summary(S, cells, rain_reg, rain_t, w),
            "analogs": None,                      # past-season analogs are Singapore readings
            "extras": dict({k: v for k, v in (base.get("extras") or {}).items() if k not in ("cams", "stations")}, **city_cams(c, log)),
            "sources": [s for s in base.get("sources", []) if "NEA" not in s.get("name", "")] + [
                {"name": "World Air Quality Index Project", "status": "ok", "detail": f"{r.get('station')}: PM2.5 {r['pm25']} ug/m3 (from US AQI)",
                 "time": r["time"]}],
        })
        d = SITE_DATA / "cities" / c["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "inputs.json").write_text(json.dumps(out, separators=(",", ":")))
        done.append(c["id"])
    (SITE_DATA / "cities" ).mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "cities" / "index.json").write_text(json.dumps({"built": datetime.now(timezone.utc).isoformat(),
        "cities": [{k: c[k] for k in ("id", "name", "country")} for c in CITIES if c["id"] in done]}))
    log(f"home cities written: {', '.join(done) or 'none'}")


def obs_block(nea, t0):
    """Observations relative to t0."""
    def series(hist, key="national"):
        out = []
        for row in hist:
            t = datetime.fromisoformat(row["t"])
            out.append(((t - t0).total_seconds() / 3600.0, row))
        return out
    psi = series(nea["psi_hist"])
    pm24 = series(nea["pm24_hist"])
    pm1 = series(nea["pm1_hist"])
    regions = ["national", "north", "south", "east", "west", "central"]
    latest_psi = psi[-1][1] if psi else {}
    latest_pm24 = pm24[-1][1] if pm24 else {}
    # 6-hour means of 1-hour PM2.5 (national) centred on past arrival times
    pm1_at = {}
    last_pm1 = pm1[-1][0] if pm1 else 0
    for a in ARRIVALS_H:
        if a > max(0, last_pm1 - 2):   # arrivals after t0 count once readings cover them (hourly refresh)
            continue
        vals = [row["national"] for h, row in pm1 if a - 3 <= h < a + 3 and "national" in row]
        if vals:
            pm1_at[str(a)] = round(float(np.mean(vals)), 1)
    return {
        "time": (t0.isoformat()),
        "psi_now": {r: latest_psi.get(r) for r in regions},
        "pm24_now": {r: latest_pm24.get(r) for r in regions},
        "pm24_h": round(pm24[-1][0], 2) if pm24 else None,     # when the latest 24-hour reading was taken, hours from t0
        "pm1_at": pm1_at,
        "psi_hist": [[round(h, 2), {r: row.get(r) for r in regions}] for h, row in psi if h >= -96],
        "pm24_hist": [[round(h, 2), row.get("national")] for h, row in pm24 if h >= -96],
    }


def region_rain(weather, cells):
    """Mean 6-hour rain over each region's fire cells (or its centre), per member and time >= -24 h."""
    lons, lats = weather["lons"], weather["lats"]
    sel = weather["times"] >= -24
    R = weather["rain"][:, sel]
    out = np.zeros((R.shape[0], R.shape[1], len(REGIONS)), np.float32)
    for ri, reg in enumerate(REGIONS):
        pts = [(c["lon"], c["lat"]) for c in cells["cells"] if c.get("region") == ri]
        if not pts:
            pts = [(reg["lon"], reg["lat"])]
        acc = 0
        for lon, lat in pts[:40]:
            ix = int(np.clip(round((lon - lons[0]) / (lons[1] - lons[0])), 0, len(lons) - 1))
            iy = int(np.clip(round((lat - lats[0]) / (lats[1] - lats[0])), 0, len(lats) - 1))
            acc = acc + R[:, :, iy, ix]
        out[:, :, ri] = acc / min(len(pts), 40)
    return out, weather["times"][sel]


def wind_display(weather):
    lons, lats = weather["lons"], weather["lats"]
    step = max(1, int(round(WIND_DISPLAY_DEG / (lons[1] - lons[0]))))
    t = weather["times"]
    keep = [i for i, h in enumerate(t) if h >= -24 and i % 2 == 0]
    sel = np.array(keep)
    u = weather["u"][:, sel][:, :, ::step, ::step]
    v = weather["v"][:, sel][:, :, ::step, ::step]
    q = np.stack([u, v], axis=2) / WIND_SCALE
    q = np.clip(np.round(q), -127, 127).astype(np.int8)
    return {"grid": {"lon0": float(lons[0]), "lat0": float(lats[0]), "d": float((lons[1] - lons[0]) * step),
                     "nx": int(u.shape[-1]), "ny": int(u.shape[-2])},
            "t_h": [float(x) for x in weather["times"][sel]], "scale": WIND_SCALE, "data": b64(q, np.int8)}


def summary(S, cells, rain_reg, rain_t, weather):
    """Compact numbers used to explain why the odds moved between runs."""
    isl = {}
    for ri, reg in enumerate(REGIONS):
        isl.setdefault(reg["island"], []).append(ri)
    frp = {k: round(sum(c["frp0"] for c in cells["cells"] if c.get("region") in v), 1) for k, v in isl.items()}
    fut = (rain_t >= 0) & (rain_t <= 120)
    rain = {k: round(float(rain_reg[:, fut][:, :, v].sum(axis=1).mean()), 1) for k, v in isl.items()}
    A0 = [i for i, a in enumerate(ARRIVALS_H) if 0 <= a <= 72]
    tot = S[:, A0].sum(axis=(0, 1, 3))
    share = {k: round(float(tot[v].sum() / max(tot.sum(), 1e-9)), 3) for k, v in isl.items()}
    return {"frp24_mw": frp, "rain_mm_0_120h": rain, "transport_share_0_72h": share}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="synthetic data, no network")
    args = ap.parse_args()
    started = time.time()
    sources = []
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    if args.sample:
        import synthetic
        t0 = round_hour(datetime.now(timezone.utc))
        nea = synthetic.nea(t0)
        dets, fstatus = synthetic.fires()
        weather = synthetic.weather(t0)
        cycle = t0 - timedelta(hours=5)
        for name in ("NEA PSI and PM2.5", "NASA FIRMS hotspots", "NOAA GEFS ensemble"):
            sources.append({"name": name, "status": "sample", "time": t0.isoformat()})
    else:
        import fetch_nea, fetch_firms, fetch_gefs
        log("NEA readings")
        nea = fetch_nea.fetch(log=log)
        t0 = round_hour(datetime.fromisoformat(nea["latest_time"]).astimezone(timezone.utc))
        sources.append({"name": "NEA PSI and PM2.5", "status": "ok", "time": nea["latest_time"]})
        log("FIRMS hotspots")
        try:
            dets, fstatus = fetch_firms.fetch(t0, log=log)
            sources.append({"name": "NASA FIRMS hotspots", "status": "ok",
                            "detail": "; ".join(f"{k} {v}" for k, v in fstatus.items()),
                            "time": datetime.now(timezone.utc).isoformat()})
            try:   # keep a copy, so a short FIRMS outage can fall back to it (cached between runs)
                (WORK / "firms_last.json").parent.mkdir(parents=True, exist_ok=True)
                (WORK / "firms_last.json").write_text(json.dumps({"t0": t0.isoformat(), "dets": dets}))
            except Exception:
                pass
        except Exception as e:
            log(f"  FIRMS failed: {e}")
            dets = []
            # fall back to the last good detections if they are under 12 hours old, ages shifted to now.
            # Marked "partial" (not "failed"), so the run publishes, and the page says which data it used.
            try:
                last = json.loads((WORK / "firms_last.json").read_text())
                age = (t0 - datetime.fromisoformat(last["t0"])).total_seconds() / 3600
                if 0 <= age <= 12:
                    dets = [dict(d, h=d["h"] + age) for d in last["dets"] if d["h"] + age <= 48]
                    log(f"  using FIRMS detections from {age:.1f} h earlier ({len(dets)})")
            except Exception:
                pass
            sources.append({"name": "NASA FIRMS hotspots", "status": "partial" if dets else "failed",
                            "detail": (f"FIRMS unreachable; using detections from the previous run, {age:.0f} h older" if dets else str(e)[:200]),
                            "time": datetime.now(timezone.utc).isoformat()})
        log("GEFS ensemble")
        g = fetch_gefs.fetch(log=log)
        cycle = g["cycle"]
        weather = fetch_gefs.to_weather(g, t0)
        import os as _os
        for model, env, label, horizon in (("aifs-ens", "ECMWF_AIFS", "ECMWF AI ensemble (AIFS ENS)", "+360 h"),
                                           ("ifs", "ECMWF_ENS", "ECMWF IFS ensemble", "+240 h")):
            if _os.environ.get(env, "1") == "0":
                continue
            try:
                import fetch_ecmwf, signal
                def _timeout(signum, frame):
                    raise TimeoutError("ECMWF download took longer than 20 minutes")
                signal.signal(signal.SIGALRM, _timeout)
                signal.alarm(20 * 60)
                try:
                    ens = fetch_ecmwf.fetch(log=log, model=model)
                finally:
                    signal.alarm(0)
                weather = fetch_ecmwf.merge(weather, ens, t0, log=log)
                sources.append({"name": label, "status": "ok", "detail": f"50 forecasts, run {ens['run']:%Y-%m-%d %H}Z, to {horizon}",
                                "time": ens["run"].isoformat()})
            except Exception as e:
                log(f"  {label} failed: {e}")
                sources.append({"name": label, "status": "failed", "detail": str(e)[:160]})
        sources.append({"name": "NOAA GEFS ensemble", "status": "ok" if not g["errors"] else "partial",
                        "detail": f"{g['files']}/{g['requested']} files, cycle {cycle:%Y-%m-%d %H}Z",
                        "time": cycle.isoformat()})
    if args.sample:
        from fetch_climate import SEED
        climate = dict(SEED)
    else:
        import fetch_climate
        try:
            climate, cstat = fetch_climate.fetch(log=log)
            sources.append({"name": "El Niño and Indian Ocean Dipole", "status": "ok" if "kept" not in cstat else "partial", "detail": cstat,
                            "time": datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            from fetch_climate import SEED
            climate = dict(SEED)
            sources.append({"name": "El Niño and Indian Ocean Dipole", "status": "failed", "detail": str(e)[:160]})
    import fetch_history
    psi_rows = nea["psi_hist"]
    psi_now = psi_rows[-1].get("national") if psi_rows else None
    psi_2d = None
    for row in psi_rows:
        if abs((datetime.fromisoformat(row["t"]) - (t0 - timedelta(hours=48))).total_seconds()) < 1800:
            psi_2d = row.get("national")
    if args.sample:
        analogs = fetch_history.sample_analogs()
    else:
        log("NEA history and past-season analogs")
        try:
            daily = fetch_history.load(log=log)
            analogs = fetch_history.analogs(daily, psi_now, psi_2d, t0.astimezone(fetch_history.SGT).date())
            sources.append({"name": "NEA PSI history (analogs)", "status": "ok" if analogs else "partial",
                            "detail": (f"{len(analogs['paths'])} similar past days from {', '.join(map(str, analogs['years']))}" if analogs else "no similar past days"),
                            "time": datetime.now(timezone.utc).isoformat()})
        except Exception as e:
            log(f"  history failed: {e}")
            analogs = None
            sources.append({"name": "NEA PSI history (analogs)", "status": "failed", "detail": str(e)[:160]})
    import fetch_extras
    if args.sample:
        extras = fetch_extras.sample(t0)
    else:
        log("extra sources")
        extras, xs = fetch_extras.fetch_all(log=log)
        sources.extend(xs)
    log(f"t0 {t0.isoformat()}, weather times {weather['times'][0]:.0f}..{weather['times'][-1]:.0f} h, "
        f"{len(weather['members'])} members")

    from fetch_firms import to_fire_list
    fire_list = to_fire_list(dets)
    cells = trajectories.make_fire_cells(fire_list, FIRE_GRID_DEG, WEST, SOUTH, EAST, NORTH)
    log(f"{len(dets)} detections -> {cells['n']} fire cells")

    log("back-tracing from Singapore")
    S, paths, path_arrivals = trajectories.footprints(weather, cells, log=log)
    rain_reg, rain_t = region_rain(weather, cells)

    frp0 = [round(sum(c["frp0"] for c in cells["cells"] if c.get("region") == ri), 1) for ri in range(len(REGIONS))]
    frp1 = [round(sum(c["frp1"] for c in cells["cells"] if c.get("region") == ri), 1) for ri in range(len(REGIONS))]
    n0 = [sum(c["n0"] for c in cells["cells"] if c.get("region") == ri) for ri in range(len(REGIONS))]

    hot = [[round(d["lon"], 3), round(d["lat"], 3), round(d["frp"], 1), round(d["h"], 1), d["sat"]]
           for d in dets if d["h"] < 24]
    out = {
        "version": 1,
        "sample": bool(args.sample),
        "built": datetime.now(timezone.utc).isoformat(),
        "t0": t0.isoformat(),
        "cycle": cycle.isoformat(),
        "members": list(weather["members"]),
        "receptor": [SG_LON, SG_LAT],
        "arrivals_h": ARRIVALS_H,
        "age_efold_h": AGE_EFOLD_H,
        "emis_bins_h": EMIS_BINS_H,
        "regions": [{**r, "frp0": frp0[i], "frp1": frp1[i], "n0": n0[i]} for i, r in enumerate(REGIONS)],
        "S": quant_log16(S),
        "rain": {"t_h": [float(x) for x in rain_t], "data": b64(rain_reg, np.float32)},
        "obs": obs_block(nea, t0),
        "hotspots": hot,
        "wind": wind_display(weather),
        "paths": {"arrivals_h": path_arrivals, "step_h": 3,
                  "members": [[[[round(x, 2), round(y, 2)] for x, y in p] for p in mem] for mem in paths]},
        "summary": summary(S, cells, rain_reg, rain_t, weather),
        "climate": {k: climate.get(k) for k in ("roni", "roni_period", "dmi", "dmi_period", "source")},
        "extras": {k: v for k, v in extras.items() if k not in ("signals", "dryness")},
        "signals": extras.get("signals"),
        "dryness": extras.get("dryness"),
        "analogs": analogs,
        "sources": sources,
        "build_seconds": round(time.time() - started, 1),
    }
    path = SITE_DATA / "inputs.json"
    path.write_text(json.dumps(out, separators=(",", ":")))
    log(f"wrote {path} ({path.stat().st_size / 1e6:.2f} MB) in {out['build_seconds']} s")
    if not args.sample and __import__("os").environ.get("CITIES", "1") != "0":
        try:
            build_cities(out, weather, cells, t0, log=log)
        except Exception as e:
            log(f"home cities failed: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
