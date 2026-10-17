"""ECMWF ensembles from ECMWF open data, cropped to SE Asia:
  ifs       the physics ensemble (50 perturbed forecasts) to +240 h
  aifs-ens  ECMWF's machine-learned ensemble (AIFS ENS, 50 forecasts) to +360 h

Open data, CC-BY-4.0, no key. Global 0.25° fields are downloaded for u/v at 850 hPa and total
precipitation every 12 hours to +240 h from the latest 00 or 12 UTC run, then cropped and thinned to
the GEFS 0.5° grid. The cropped result is saved under .work/ecmwf so the workflow can cache it and
skip the ~3 GB download on runs that reuse the same ECMWF run.

Beyond +240 h (the end of the open ENS data) each ECMWF forecast is continued by a GEFS forecast.
"""
import os
from datetime import timedelta, timezone

import numpy as np

from config import WEST, EAST, SOUTH, NORTH, WORK

STEPS = list(range(0, 241, 12))
STEPS_BY_MODEL = {"ifs": STEPS, "aifs-ens": list(range(0, 361, 12))}
DIR = WORK / "ecmwf"


def _crop(vals, nj, ni, la1, lo1, dlat, dlon):
    grid = vals.reshape(nj, ni)
    lats = la1 + np.arange(nj) * dlat
    lons = (lo1 + np.arange(ni) * dlon) % 360
    iy = np.where((lats >= SOUTH - 1e-6) & (lats <= NORTH + 1e-6))[0]
    ix = np.where((lons >= WEST - 1e-6) & (lons <= EAST + 1e-6))[0]
    sub = grid[np.ix_(iy, ix)]
    la, lo = lats[iy], lons[ix]
    if la[0] > la[-1]:
        sub, la = sub[::-1], la[::-1]
    # thin 0.25 -> 0.5 degree to match GEFS
    keep_y = np.where(np.isclose((la - SOUTH) / 0.5, np.round((la - SOUTH) / 0.5)))[0]
    keep_x = np.where(np.isclose((lo - WEST) / 0.5, np.round((lo - WEST) / 0.5)))[0]
    return sub[np.ix_(keep_y, keep_x)], la[keep_y], lo[keep_x]


def _decode(path, want):
    import eccodes
    out = {}
    lats = lons = None
    with open(path, "rb") as f:
        while True:
            gid = eccodes.codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                sn = eccodes.codes_get(gid, "shortName")
                if sn not in want:
                    continue
                num = eccodes.codes_get(gid, "number") - 1
                step = eccodes.codes_get(gid, "endStep")
                ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
                la1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lo1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                dlat = eccodes.codes_get(gid, "jDirectionIncrementInDegrees") * (1 if eccodes.codes_get(gid, "jScansPositively") else -1)
                dlon = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
                sub, lats, lons = _crop(eccodes.codes_get_values(gid), nj, ni, la1, lo1, dlat, dlon)
                out[(sn, num, step)] = sub.astype(np.float32)
            finally:
                eccodes.codes_release(gid)
    return out, lats, lons


MIRRORS = ["https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com", "https://ai4edataeuwest.blob.core.windows.net/ecmwf",
           "https://data.ecmwf.int/forecasts"]
_S = None


def _session():
    global _S
    if _S is None:
        import requests
        _S = requests.Session()
        _S.mount("https://", requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=16))
    return _S


def _get(path, rng=None, tries=8):
    """One request at a time per field, backing off when a mirror asks us to slow down."""
    import time as _t
    last = None
    for k in range(tries):
        base = MIRRORS[k % len(MIRRORS)] if k >= 3 else MIRRORS[0]
        try:
            r = _session().get(f"{base}/{path}", timeout=120,
                               headers={"Range": f"bytes={rng[0]}-{rng[0] + rng[1] - 1}"} if rng else None)
            if r.status_code in (200, 206):
                return r.content
            if r.status_code == 404:
                return None
            last = r.status_code
        except Exception as e:
            last = str(e)[:80]
        _t.sleep(min(30, 1.5 * 2 ** k))
    raise RuntimeError(f"ECMWF download failed: {path} ({last})")


def _path(model, run, step, kind):
    return f"{run:%Y%m%d}/{run:%H}z/{model}/0p25/enfo/{run:%Y%m%d%H}0000-{step}h-enfo-{kind}"


def _latest(model, steps, log):
    from datetime import datetime, timezone as tz
    now = datetime.now(tz.utc)
    t = now.replace(minute=0, second=0, microsecond=0)
    t = t - timedelta(hours=t.hour % 6)
    for k in range(0, 9):
        run = t - timedelta(hours=6 * k)
        for kind in ("ef", "pf"):
            idx = _get(_path(model, run, steps[-1], kind) + ".index", tries=3)
            if idx:
                return run, kind
    raise RuntimeError(f"no recent {model} run found on the ECMWF mirrors")


def fetch(log=print, model="ifs"):
    import json as _json
    import eccodes
    from concurrent.futures import ThreadPoolExecutor
    steps = STEPS_BY_MODEL[model]
    run, kind = _latest(model, steps, log)
    run = run.replace(tzinfo=timezone.utc)
    DIR.mkdir(parents=True, exist_ok=True)
    cache = DIR / f"{model}_{run:%Y%m%d%H}.npz"
    if cache.exists():
        z = np.load(cache)
        log(f"  ECMWF {model} {run:%Y-%m-%d %H}Z from cache")
        return {"model": model, "run": run, "steps": list(z["steps"]), "lats": z["lats"], "lons": z["lons"], "U": z["U"], "V": z["V"], "R": z["R"]}
    log(f"  ECMWF {model} {run:%Y-%m-%d %H}Z: fetching u/v 850 hPa and rain for 50 members, {len(steps)} steps")
    jobs = []
    for st in steps:
        raw = _get(_path(model, run, st, kind) + ".index")
        if not raw:
            continue
        for line in raw.decode().splitlines():
            try:
                e = _json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "pf":
                continue
            par = e.get("param")
            if (par in ("u", "v") and str(e.get("levelist")) == "850") or (par == "tp" and st > 0):
                jobs.append((par, int(e["number"]) - 1, st, _path(model, run, st, kind) + ".grib2", (int(e["_offset"]), int(e["_length"]))))
    if not jobs:
        raise RuntimeError(f"{model}: no u/v 850 or tp fields listed in the index")
    lats = lons = None
    uv, tp = {}, {}

    def one(j):
        par, num, st, path, rng = j
        data = _get(path, rng)
        if not data:
            return None
        gid = eccodes.codes_new_from_message(data)
        try:
            ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
            la1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
            lo1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
            dlat = eccodes.codes_get(gid, "jDirectionIncrementInDegrees") * (1 if eccodes.codes_get(gid, "jScansPositively") else -1)
            dlon = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
            sub, la, lo = _crop(eccodes.codes_get_values(gid), nj, ni, la1, lo1, dlat, dlon)
        finally:
            eccodes.codes_release(gid)
        return par, num, st, sub.astype(np.float32), la, lo

    import time as _t
    t0 = _t.time()
    with ThreadPoolExecutor(6) as ex:
        for res in ex.map(one, jobs):
            if res is None:
                continue
            par, num, st, sub, la, lo = res
            lats, lons = la, lo
            (tp if par == "tp" else uv)[(par, num, st)] = sub
    log(f"  ECMWF {model}: {len(uv) + len(tp)}/{len(jobs)} fields in {_t.time() - t0:.0f}s")
    scale = 1.0 if model == "aifs-ens" else 1000.0   # AIFS gives rain in kg/m2 (= mm); IFS in metres
    M, T = 50, len(steps)
    ny, nx = len(lats), len(lons)
    U = np.full((M, T, ny, nx), np.nan, np.float32)
    V = np.full_like(U, np.nan)
    R = np.zeros_like(U)
    for m in range(M):
        prev = 0.0        # rain is accumulated from the start of the run
        for ti, st in enumerate(steps):
            if ("u", m, st) in uv:
                U[m, ti] = uv[("u", m, st)]
                V[m, ti] = uv[("v", m, st)]
            acc = tp.get(("tp", m, st))
            if acc is not None:
                if prev is not None and ti > 0:
                    hrs = steps[ti] - steps[ti - 1]
                    R[m, ti] = np.maximum(acc - prev, 0) * scale / hrs * 6.0      # mm per 6 h
                prev = acc
    from fetch_gefs import _fill
    _fill(U)
    _fill(V)
    np.savez_compressed(cache, steps=np.array(steps), lats=lats, lons=lons, U=U, V=V, R=R)
    log(f"  ECMWF: {int((~np.isnan(U[:, :, 0, 0])).sum())}/{M * T} member-steps decoded")
    return {"model": model, "run": run, "steps": steps, "lats": lats, "lons": lons, "U": U, "V": V, "R": R}


def merge(weather, ens, t0, log=print):
    """Add ECMWF members to a GEFS weather dict (same grid, same time axis)."""
    times = weather["times"]
    Mg = len(weather["members"])
    lat_ok = len(ens["lats"]) == len(weather["lats"]) and np.allclose(ens["lats"], weather["lats"])
    lon_ok = len(ens["lons"]) == len(weather["lons"]) and np.allclose(ens["lons"], weather["lons"])
    if not (lat_ok and lon_ok):
        raise RuntimeError("ECMWF grid does not match GEFS grid")
    eh = np.array([((ens["run"] + timedelta(hours=int(s))) - t0).total_seconds() / 3600 for s in ens["steps"]])
    Me = ens["U"].shape[0]
    U = np.empty((Me,) + weather["u"].shape[1:], np.float32)
    V = np.empty_like(U)
    R = np.empty_like(U)
    for k in range(Me):
        g = k % Mg          # GEFS partner for times outside the ECMWF window
        U[k], V[k], R[k] = weather["u"][g], weather["v"][g], weather["rain"][g]
        for ti, h in enumerate(times):
            if h < eh[0] or h > eh[-1]:
                continue
            j = int(np.clip(np.searchsorted(eh, h), 1, len(eh) - 1))
            a, b = eh[j - 1], eh[j]
            w = 0.0 if b == a else (h - a) / (b - a)
            U[k, ti] = ens["U"][k, j - 1] * (1 - w) + ens["U"][k, j] * w
            V[k, ti] = ens["V"][k, j - 1] * (1 - w) + ens["V"][k, j] * w
            R[k, ti] = ens["R"][k, j]
    out = dict(weather)
    pre = "a" if ens.get("model") == "aifs-ens" else "e"
    out["members"] = list(weather["members"]) + [f"{pre}{k:02d}" for k in range(Me)]
    out["u"] = np.concatenate([weather["u"], U])
    out["v"] = np.concatenate([weather["v"], V])
    out["rain"] = np.concatenate([weather["rain"], R])
    log(f"  merged ECMWF: {len(out['members'])} weather forecasts in total")
    return out
