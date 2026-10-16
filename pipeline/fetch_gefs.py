"""NOAA GEFS ensemble (31 members, 0.5 degree) cropped to SE Asia via the NOMADS grib filter.

Fetches u/v winds at 925 and 850 hPa and accumulated precipitation for every member,
every 6 hours out to +150 h, plus 6-hour control forecasts from earlier cycles so the
back-traces can reach into the past.

Server rules: NOMADS blocks heavy users, so requests are throttled to about one per second.
"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from net import get
from config import (WEST, EAST, SOUTH, NORTH, GEFS_MEMBERS, GEFS_STEP_H, GEFS_MAX_H,
                    GEFS_HISTORY_CYCLES, WORK)

FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p50a.pl"
LISTING = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gens/prod/gefs.{d}/{h}/atmos/pgrb2ap5/"


def _url(cycle, member, fhr):
    d, h = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    prefix = "gec00" if member == "c00" else f"ge{member}"
    file = f"{prefix}.t{h}z.pgrb2a.0p50.f{fhr:03d}"
    q = (f"?dir=%2Fgefs.{d}%2F{h}%2Fatmos%2Fpgrb2ap5&file={file}"
         "&var_UGRD=on&var_VGRD=on&var_APCP=on"
         "&lev_925_mb=on&lev_850_mb=on&lev_surface=on"
         f"&subregion=&toplat={NORTH:g}&leftlon={WEST:g}&rightlon={EAST:g}&bottomlat={SOUTH:g}")
    return FILTER + q


def latest_cycle(now=None, log=print):
    """Newest cycle whose last perturbed member has reached GEFS_MAX_H."""
    now = now or datetime.now(timezone.utc)
    c = now.replace(minute=0, second=0, microsecond=0)
    c = c - timedelta(hours=c.hour % 6)
    for _ in range(8):
        d, h = c.strftime("%Y%m%d"), c.strftime("%H")
        try:
            r = get(LISTING.format(d=d, h=h), timeout=60, retries=1, ok_404=True, min_interval=1.0,
                    host_key="nomads")
            if r is not None and f"gep30.t{h}z.pgrb2a.0p50.f{GEFS_MAX_H:03d}" in r.text:
                return c
        except Exception as e:
            log(f"  GEFS listing {d}/{h}: {e}")
        c -= timedelta(hours=6)
    raise RuntimeError("no complete GEFS cycle found in the last 48 hours")


def _download(args):
    url, path = args
    if path.exists() and path.stat().st_size > 1000:
        return path, None
    try:
        r = get(url, timeout=90, retries=4, min_interval=1.1, host_key="nomads")
        if not r.content.startswith(b"GRIB"):
            return path, "not GRIB: " + r.text[:100]
        path.write_bytes(r.content)
        return path, None
    except Exception as e:
        return path, str(e)[:160]


def decode(path):
    """-> dict with u925, v925, u850, v850 (m/s), tp_rate (mm/h) on [lat asc, lon asc], plus grid."""
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
                lev = eccodes.codes_get(gid, "level")
                ni = eccodes.codes_get(gid, "Ni")
                nj = eccodes.codes_get(gid, "Nj")
                vals = eccodes.codes_get_values(gid).reshape(nj, ni)
                la1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                la2 = eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees")
                lo1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                lo2 = eccodes.codes_get(gid, "longitudeOfLastGridPointInDegrees")
                if la1 > la2:
                    vals = vals[::-1]
                    la1, la2 = la2, la1
                lats = np.linspace(la1, la2, nj)
                lons = np.linspace(lo1, lo2, ni)
                if sn in ("u", "v") and lev in (925, 850):
                    out[f"{sn}{lev}"] = vals
                elif sn in ("tp", "apcp") or (eccodes.codes_get(gid, "discipline") == 0
                                               and eccodes.codes_get(gid, "parameterCategory") == 1
                                               and eccodes.codes_get(gid, "parameterNumber") == 8):
                    s0 = eccodes.codes_get(gid, "startStep")
                    s1 = eccodes.codes_get(gid, "endStep")
                    hrs = max(1, s1 - s0)
                    out["tp_rate"] = np.maximum(vals, 0) / hrs
            finally:
                eccodes.codes_release(gid)
    return out, lats, lons


def fetch(log=print, workers=2):
    cycle = latest_cycle(log=log)
    log(f"  GEFS cycle {cycle:%Y-%m-%d %H}Z")
    base = WORK / "gefs" / cycle.strftime("%Y%m%d%H")
    base.mkdir(parents=True, exist_ok=True)
    steps = list(range(0, GEFS_MAX_H + 1, GEFS_STEP_H))
    jobs = []
    for m in GEFS_MEMBERS:
        for f in steps:
            jobs.append((_url(cycle, m, f), base / f"{m}_f{f:03d}.grb2"))
    hist = []
    for k in range(1, GEFS_HISTORY_CYCLES + 1):
        c = cycle - timedelta(hours=6 * k)
        p = WORK / "gefs" / "hist" / f"{c:%Y%m%d%H}_c00_f006.grb2"
        p.parent.mkdir(parents=True, exist_ok=True)
        hist.append((c, p))
        jobs.append((_url(c, "c00", 6), p))
    t_start = time.time()
    errors = []
    with ThreadPoolExecutor(workers) as ex:
        for i, (path, err) in enumerate(ex.map(_download, jobs)):
            if err:
                errors.append(f"{path.name}: {err}")
            if (i + 1) % 100 == 0:
                log(f"  GEFS downloaded {i + 1}/{len(jobs)} ({time.time() - t_start:.0f}s)")
    log(f"  GEFS: {len(jobs) - len(errors)}/{len(jobs)} files ok")
    if errors:
        log("  first errors: " + " | ".join(errors[:3]))

    # assemble arrays [M, T, ny, nx]
    lats = lons = None
    M, T = len(GEFS_MEMBERS), len(steps)
    U = V = R = None
    missing = 0
    for mi, m in enumerate(GEFS_MEMBERS):
        for ti, f in enumerate(steps):
            path = base / f"{m}_f{f:03d}.grb2"
            if not path.exists():
                missing += 1
                continue
            try:
                d, la, lo = decode(path)
            except Exception as e:
                errors.append(f"decode {path.name}: {e}")
                missing += 1
                continue
            if U is None:
                lats, lons = la, lo
                U = np.full((M, T, len(la), len(lo)), np.nan, np.float32)
                V = np.full_like(U, np.nan)
                R = np.full_like(U, np.nan)
            if "u925" in d and "u850" in d:
                U[mi, ti] = 0.5 * (d["u925"] + d["u850"])
                V[mi, ti] = 0.5 * (d["v925"] + d["v850"])
            if "tp_rate" in d:
                R[mi, ti] = d["tp_rate"] * 6.0
    if U is None:
        raise RuntimeError("no GEFS files could be decoded; " + "; ".join(errors[:3]))
    frac_missing = np.isnan(U[:, :, 0, 0]).mean()
    if frac_missing > 0.3:
        raise RuntimeError(f"too much GEFS data missing ({frac_missing:.0%})")
    _fill(U)
    _fill(V)
    R[:, 0] = 0.0
    _fill(R, zero_ok=True)

    # history (control 6 h forecasts from earlier cycles) -> valid times cycle-6k+6
    hU, hV, hR, ht = [], [], [], []
    for c, p in sorted(hist):
        valid = c + timedelta(hours=6)
        if not p.exists():
            continue
        try:
            d, la, lo = decode(p)
        except Exception:
            continue
        if "u925" not in d:
            continue
        hU.append(0.5 * (d["u925"] + d["u850"]))
        hV.append(0.5 * (d["v925"] + d["v850"]))
        hR.append(d.get("tp_rate", np.zeros_like(d["u925"])) * 6.0)
        ht.append(valid)
    return {"cycle": cycle, "steps": steps, "lats": lats, "lons": lons, "U": U, "V": V, "R": R,
            "hist": {"times": ht, "U": hU, "V": hV, "R": hR}, "members": GEFS_MEMBERS,
            "errors": errors, "files": len(jobs) - len(errors), "requested": len(jobs)}


def _fill(A, zero_ok=False):
    """Fill missing member/steps: time-interpolate, then member mean."""
    M, T = A.shape[:2]
    for m in range(M):
        ok = ~np.isnan(A[m, :, 0, 0])
        if ok.all() or not ok.any():
            continue
        idx = np.arange(T)
        good = idx[ok]
        for t in idx[~ok]:
            j = np.searchsorted(good, t)
            if j == 0:
                A[m, t] = A[m, good[0]]
            elif j >= len(good):
                A[m, t] = A[m, good[-1]]
            else:
                a, b = good[j - 1], good[j]
                w = (t - a) / (b - a)
                A[m, t] = A[m, a] * (1 - w) + A[m, b] * w
    mean = np.nanmean(A, axis=0)
    for m in range(M):
        bad = np.isnan(A[m, :, 0, 0])
        A[m, bad] = mean[bad]
    if zero_ok:
        np.nan_to_num(A, copy=False)


def to_weather(g, t0):
    """Merge history + forecast onto one 6-hourly time axis, hours relative to t0."""
    cyc = g["cycle"]
    M = len(g["members"])
    times, U, V, R = [], [], [], []
    for valid, hu, hv, hr in zip(g["hist"]["times"], g["hist"]["U"], g["hist"]["V"], g["hist"]["R"]):
        if valid >= cyc:
            continue
        times.append((valid - t0).total_seconds() / 3600)
        U.append(np.broadcast_to(hu, (M,) + hu.shape))
        V.append(np.broadcast_to(hv, (M,) + hv.shape))
        R.append(np.broadcast_to(hr, (M,) + hr.shape))
    rain_at_cycle = None
    for valid, hr in zip(g["hist"]["times"], g["hist"]["R"]):
        if valid == cyc:
            rain_at_cycle = hr
    for ti, f in enumerate(g["steps"]):
        valid = cyc + timedelta(hours=f)
        times.append((valid - t0).total_seconds() / 3600)
        U.append(g["U"][:, ti])
        V.append(g["V"][:, ti])
        r = g["R"][:, ti]
        if ti == 0 and rain_at_cycle is not None:
            r = np.broadcast_to(rain_at_cycle, r.shape)
        R.append(r)
    order = np.argsort(times)
    times = np.array(times)[order]
    # require even spacing; if history has gaps, interpolate onto a regular axis
    U = np.stack(U, axis=1)[:, order]
    V = np.stack(V, axis=1)[:, order]
    R = np.stack(R, axis=1)[:, order]
    reg = np.arange(times[0], times[-1] + 0.01, 6.0)
    if len(reg) != len(times) or np.abs(reg - times).max() > 0.01:
        U, V, R = (_regrid_time(A, times, reg) for A in (U, V, R))
        times = reg
    return {"members": g["members"], "lons": g["lons"], "lats": g["lats"], "times": times,
            "u": np.ascontiguousarray(U, dtype=np.float32), "v": np.ascontiguousarray(V, dtype=np.float32),
            "rain": np.ascontiguousarray(R, dtype=np.float32)}


def _regrid_time(A, t, reg):
    out = np.empty(A.shape[:1] + (len(reg),) + A.shape[2:], np.float32)
    for i, x in enumerate(reg):
        j = np.clip(np.searchsorted(t, x), 1, len(t) - 1)
        a, b = t[j - 1], t[j]
        w = 0 if b == a else np.clip((x - a) / (b - a), 0, 1)
        out[:, i] = A[:, j - 1] * (1 - w) + A[:, j] * w
    return out
