"""ECMWF IFS ensemble (51 forecasts) from ECMWF open data, cropped to SE Asia.

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
                num = eccodes.codes_get(gid, "number") if eccodes.codes_get(gid, "dataType") == "pf" else 0
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


def fetch(log=print):
    from ecmwf.opendata import Client
    client = None
    run = None
    for src in ("aws", "ecmwf"):
        try:
            c = Client(source=src, model="ifs", resol="0p25", maximum_retries=3, retry_after=15)
            run = c.latest(type="pf", stream="enfo", step=240, param="tp")
            client = c
            break
        except Exception as e:
            log(f"  ECMWF via {src}: {e}")
    if client is None:
        raise RuntimeError("no ECMWF open-data source reachable")
    run = run.replace(tzinfo=timezone.utc)
    DIR.mkdir(parents=True, exist_ok=True)
    cache = DIR / f"ens_{run:%Y%m%d%H}.npz"
    if cache.exists():
        z = np.load(cache)
        log(f"  ECMWF ENS {run:%Y-%m-%d %H}Z from cache")
        return {"run": run, "steps": list(z["steps"]), "lats": z["lats"], "lons": z["lons"], "U": z["U"], "V": z["V"], "R": z["R"]}
    log(f"  ECMWF ENS {run:%Y-%m-%d %H}Z: downloading u/v 850 hPa and rain, 51 members, {len(STEPS)} steps")
    pl, sfc = DIR / "pl.grib2", DIR / "sfc.grib2"
    base = dict(date=run.strftime("%Y%m%d"), time=run.hour, stream="enfo", type=["cf", "pf"], step=STEPS)
    client.retrieve(**base, levtype="pl", levelist=[850], param=["u", "v"], target=str(pl))
    client.retrieve(**base, levtype="sfc", param=["tp"], target=str(sfc))
    uv, lats, lons = _decode(pl, {"u", "v"})
    tp, _, _ = _decode(sfc, {"tp"})
    for p in (pl, sfc):
        try:
            os.remove(p)
        except OSError:
            pass
    M, T = 51, len(STEPS)
    ny, nx = len(lats), len(lons)
    U = np.full((M, T, ny, nx), np.nan, np.float32)
    V = np.full_like(U, np.nan)
    R = np.zeros_like(U)
    for m in range(M):
        prev = None
        for ti, st in enumerate(STEPS):
            if ("u", m, st) in uv:
                U[m, ti] = uv[("u", m, st)]
                V[m, ti] = uv[("v", m, st)]
            acc = tp.get(("tp", m, st))
            if acc is not None:
                if prev is not None and ti > 0:
                    hrs = STEPS[ti] - STEPS[ti - 1]
                    R[m, ti] = np.maximum(acc - prev, 0) * 1000.0 / hrs * 6.0      # mm per 6 h
                prev = acc
    from fetch_gefs import _fill
    _fill(U)
    _fill(V)
    np.savez_compressed(cache, steps=np.array(STEPS), lats=lats, lons=lons, U=U, V=V, R=R)
    log(f"  ECMWF: {int((~np.isnan(U[:, :, 0, 0])).sum())}/{M * T} member-steps decoded")
    return {"run": run, "steps": STEPS, "lats": lats, "lons": lons, "U": U, "V": V, "R": R}


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
    out["members"] = list(weather["members"]) + [f"e{k:02d}" for k in range(Me)]
    out["u"] = np.concatenate([weather["u"], U])
    out["v"] = np.concatenate([weather["v"], V])
    out["rain"] = np.concatenate([weather["rain"], R])
    log(f"  merged ECMWF: {len(out['members'])} weather forecasts in total")
    return out
