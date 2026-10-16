"""Historical GEFS ensembles from NOAA's public archives on AWS, for replaying past haze seasons.

Two archives:
  reforecast  s3://noaa-gefs-retrospective/GEFSv12/reforecast  (2000-2019; GEFS v12 re-run on past days,
              00Z only, 5 members c00 p01-p04, 0.25 deg to day 10 and 0.5 deg to day 16, one file per variable)
  operational s3://noaa-gefs-pds                                 (GEFS v12 from late 2020: 0.5 deg, 31 members,
              one file per member and step)

Only the GRIB messages we need are fetched, using HTTP byte ranges from the .idx files.
Output matches fetch_gefs.fetch() so to_weather() and the rest of the pipeline work unchanged.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import numpy as np
import requests

from config import WEST, EAST, SOUTH, NORTH, GEFS_STEPS, WORK
from fetch_gefs import decode, _fill

RETRO = "https://noaa-gefs-retrospective.s3.amazonaws.com/GEFSv12/reforecast"
PDS = "https://noaa-gefs-pds.s3.amazonaws.com"
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=3))


def _get(url, rng=None, tries=4):
    for k in range(tries):
        try:
            r = SESSION.get(url, headers={"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else None, timeout=120)
            if r.status_code in (200, 206):
                return r.content
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (k + 1))
    return None


def _idx(url):
    """-> list of (start, end_or_None, description) for each GRIB message."""
    raw = _get(url + ".idx")
    if not raw:
        return []
    lines = [l.split(":", 2) for l in raw.decode().strip().splitlines()]
    out = []
    for i, (n, start, rest) in enumerate(lines):
        end = int(lines[i + 1][1]) - 1 if i + 1 < len(lines) else None
        out.append((int(start), end, rest))
    return out


def _range(url, entry):
    s, e, _ = entry
    if e is None:
        e = s + 20_000_000
    return _get(url, (s, e))


def _crop(vals, lats, lons):
    """Crop a decoded field to the domain on the 0.5 degree grid used everywhere else."""
    def keep(x, lo, hi):
        f = (x - lo) / 0.5
        return np.where((x >= lo - 1e-6) & (x <= hi + 1e-6) & (np.abs(f - np.round(f)) < 1e-3))[0]
    iy, ix = keep(lats, SOUTH, NORTH), keep(lons % 360, WEST, EAST)
    return vals[np.ix_(iy, ix)], lats[iy], (lons % 360)[ix]


def _decode_cropped(path):
    d, lats, lons = decode(path)
    out = {}
    la = lo = None
    for k, v in d.items():
        out[k], la, lo = _crop(v, lats, lons)
    return out, la, lo


def _find(entries, var, level, step, acc=False):
    for ent in entries:
        desc = ent[2]
        if acc:
            if f"{var}:surface:" in desc and re.search(rf":{step - 6}-{step} hour acc", desc):
                return ent
        elif f":{var}:{level} mb:{step} hour fcst" in desc or desc.startswith(f"{var}:{level} mb:{step} hour fcst"):
            return ent
    return None


# ---------------- reforecast (2000-2019) ----------------

def _retro_url(run, member, var, step):
    d = run.strftime("%Y%m%d%H")
    days = "Days%3A1-10" if step <= 240 else "Days%3A10-16"
    return f"{RETRO}/{run:%Y}/{d}/{member}/{days}/{var}_{'sfc' if var == 'apcp' else 'pres'}_{d}_{member}.grib2"


def _retro_messages(run, member, steps, idx_cache):
    """-> {step: bytes of u925,u850,v925,v850,apcp messages}"""
    out = {s: [] for s in steps}
    jobs = []
    for var, name, levels, acc in (("ugrd", "UGRD", (925, 850), False), ("vgrd", "VGRD", (925, 850), False),
                                   ("apcp", "APCP", (None,), True)):
        for s in steps:
            if s == 0:
                continue
            url = _retro_url(run, member, var, s)
            if url not in idx_cache:
                idx_cache[url] = _idx(url)
            for lev in levels:
                ent = _find(idx_cache[url], name, lev, s, acc)
                if ent:
                    jobs.append((s, url, ent))
    with ThreadPoolExecutor(16) as ex:
        for (s, url, ent), data in zip(jobs, ex.map(lambda j: _range(j[1], j[2]), jobs)):
            if data:
                out[s].append(data)
    return out


# ---------------- operational archive (late 2020 onwards) ----------------

def _pds_url(cycle, member, step):
    d, h = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    prefix = "gec00" if member == "c00" else f"ge{member}"
    return f"{PDS}/gefs.{d}/{h}/atmos/pgrb2ap5/{prefix}.t{h}z.pgrb2a.0p50.f{step:03d}"


def _pds_messages(cycle, member, steps):
    out = {s: [] for s in steps}

    def one(s):
        url = _pds_url(cycle, member, s)
        ents = _idx(url)
        parts = []
        for name, lev in (("UGRD", 925), ("UGRD", 850), ("VGRD", 925), ("VGRD", 850)):
            e = _find(ents, name, lev, s)
            if e:
                parts.append(_range(url, e))
        for e in ents:
            if ":APCP:surface:" in e[2]:
                parts.append(_range(url, e))
                break
        return s, [p for p in parts if p]

    with ThreadPoolExecutor(12) as ex:
        for s, parts in ex.map(one, steps):
            out[s] = parts
    return out


# ---------------- assemble ----------------

def fetch(cycle, archive, members, log=print, hist_days=4):
    """Ensemble for one 00Z cycle from the chosen archive, plus control 6-24 h forecasts for the 4 days before."""
    steps = list(GEFS_STEPS)
    base = WORK / "replay" / f"{cycle:%Y%m%d%H}"
    base.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    idx_cache = {}
    M, T = len(members), len(steps)
    U = V = R = None
    lats = lons = None
    files = 0
    for mi, m in enumerate(members):
        msgs = _retro_messages(cycle, m, steps, idx_cache) if archive == "reforecast" else _pds_messages(cycle, m, steps)
        for ti, s in enumerate(steps):
            if not msgs.get(s):
                continue
            p = base / f"{m}_{s:03d}.grb2"
            p.write_bytes(b"".join(msgs[s]))
            try:
                d, la, lo = _decode_cropped(p)
            except Exception as e:
                log(f"    decode {m} f{s}: {e}")
                continue
            finally:
                p.unlink(missing_ok=True)
            if "u925" not in d or "u850" not in d:
                continue
            if U is None:
                lats, lons = la, lo
                U = np.full((M, T, len(la), len(lo)), np.nan, np.float32)
                V = np.full_like(U, np.nan)
                R = np.full_like(U, np.nan)
            if d["u925"].shape != U.shape[2:]:
                continue
            U[mi, ti] = 0.5 * (d["u925"] + d["u850"])
            V[mi, ti] = 0.5 * (d["v925"] + d["v850"])
            if "tp_rate" in d:
                R[mi, ti] = d["tp_rate"] * 6.0
            files += 1
        log(f"    member {m}: done ({time.time() - t_start:.0f}s)")
    if U is None:
        raise RuntimeError(f"no {archive} GEFS data for {cycle:%Y-%m-%d %H}Z")
    _fill(U)
    _fill(V)
    R[:, 0] = 0.0
    _fill(R, zero_ok=True)

    # control forecasts from the previous days' 00Z runs, leads 6-24 h, so back-traces can reach 4 days into the past
    hU, hV, hR, ht = [], [], [], []
    for k in range(hist_days, 0, -1):
        run = cycle - timedelta(days=k)
        lead_steps = [6, 12, 18, 24]
        msgs = _retro_messages(run, "c00", lead_steps, idx_cache) if archive == "reforecast" else _pds_messages(run, "c00", lead_steps)
        for s in lead_steps:
            if not msgs.get(s):
                continue
            p = base / f"hist_{run:%Y%m%d}_{s:03d}.grb2"
            p.write_bytes(b"".join(msgs[s]))
            try:
                d, la, lo = _decode_cropped(p)
            except Exception:
                continue
            finally:
                p.unlink(missing_ok=True)
            if "u925" not in d or d["u925"].shape != U.shape[2:]:
                continue
            hU.append(0.5 * (d["u925"] + d["u850"]))
            hV.append(0.5 * (d["v925"] + d["v850"]))
            hR.append(d.get("tp_rate", np.zeros_like(d["u925"])) * 6.0)
            ht.append(run + timedelta(hours=s))
    log(f"    {archive}: {files}/{M * T} member-steps, {len(ht)} history steps, {time.time() - t_start:.0f}s")
    return {"cycle": cycle, "steps": steps, "lats": lats, "lons": lons, "U": U, "V": V, "R": R,
            "hist": {"times": ht, "U": hU, "V": hV, "R": hR}, "members": list(members),
            "errors": [], "files": files, "requested": M * T}
