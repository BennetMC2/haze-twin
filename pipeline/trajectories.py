"""Back-trace air from Singapore through every ensemble member.

For each weather member and each arrival time, particles are released at
Singapore and followed backwards in time through that member's winds. Where a
particle passes over a fire cell, it picks up sensitivity to that cell's
emissions, reduced by rain along the way (wet removal) and by age.

Output, per member:
  S[member, arrival, region, emission_bin]  FRP-weighted sensitivity
  mean back-trajectory paths (for drawing on the map)
"""
import numpy as np

from config import (ARRIVALS_H, EMIS_BINS_H, BACK_HOURS, PARTICLES, TURB_MS,
                    WET_LAMBDA, AGE_EFOLD_H, SG_LON, SG_LAT, REGIONS)

M_PER_DEG_LAT = 110540.0
M_PER_DEG_LON = 111320.0


def _interp(field, lons, lats, times, plon, plat, pt):
    """Trilinear interpolation of field[T, ny, nx] at particle positions/times."""
    nx, ny, nt = len(lons), len(lats), len(times)
    dx = lons[1] - lons[0]
    dy = lats[1] - lats[0]
    dt = times[1] - times[0]
    fx = np.clip((plon - lons[0]) / dx, 0, nx - 1.000001)
    fy = np.clip((plat - lats[0]) / dy, 0, ny - 1.000001)
    ft = np.clip((pt - times[0]) / dt, 0, nt - 1.000001)
    ix, iy, it = fx.astype(int), fy.astype(int), ft.astype(int)
    wx, wy, wt = fx - ix, fy - iy, ft - it
    out = 0.0
    for a, wa in ((0, 1 - wt), (1, wt)):
        ti = it + a
        v = (field[ti, iy, ix] * (1 - wx) * (1 - wy) + field[ti, iy, ix + 1] * wx * (1 - wy)
             + field[ti, iy + 1, ix] * (1 - wx) * wy + field[ti, iy + 1, ix + 1] * wx * wy)
        out = out + v * wa
    return out


def trace_member(u, v, rain, lons, lats, times, cells, rng, path_arrivals):
    """Trace one member.

    u, v: [T, ny, nx] m/s; rain: [T, ny, nx] mm per 6 h; times: hours rel. t0.
    cells: dict with 'ix','iy' (fire-cell grid indices), 'grid' (lon0, lat0, d, nx, ny).
    Returns sens[A, B, ncell] and mean paths for the requested arrivals.
    """
    A = len(ARRIVALS_H)
    B = len(EMIS_BINS_H)
    g = cells["grid"]
    cell_lookup = cells["lookup"]          # dense [ny, nx] -> fire-cell index or -1
    ncell = cells["n"]
    sens = np.zeros((A, B, ncell))
    P = PARTICLES
    arr = np.repeat(np.array(ARRIVALS_H, dtype=float), P)
    aidx = np.repeat(np.arange(A), P)
    plon = SG_LON + rng.normal(0, 0.05, A * P)
    plat = SG_LAT + rng.normal(0, 0.04, A * P)
    pt = arr.copy()
    cumrain = np.zeros(A * P)
    alive = np.ones(A * P, dtype=bool)
    rain_per_h = rain / 6.0
    paths = {a: [] for a in path_arrivals}
    path_rows = {a: np.where(aidx == ARRIVALS_H.index(a))[0] for a in path_arrivals}
    for step in range(BACK_HOURS + 1):
        if step % 3 == 0:
            for a in path_arrivals:
                rows = path_rows[a]
                ok = alive[rows]
                if ok.any():
                    paths[a].append((float(np.mean(plon[rows][ok])), float(np.mean(plat[rows][ok]))))
        if step == BACK_HOURS:
            break
        age = arr - pt
        # accumulate sensitivity where particles sit over fire cells
        ix = np.floor((plon - g["lon0"]) / g["d"]).astype(int)
        iy = np.floor((plat - g["lat0"]) / g["d"]).astype(int)
        inside = alive & (ix >= 0) & (ix < g["nx"]) & (iy >= 0) & (iy < g["ny"])
        ci = np.full(plon.shape, -1)
        ci[inside] = cell_lookup[iy[inside], ix[inside]]
        hit = ci >= 0
        if hit.any():
            w = np.exp(-age[hit] / AGE_EFOLD_H) * np.exp(-WET_LAMBDA * cumrain[hit]) / P
            b = np.clip(np.searchsorted(EMIS_BINS_H, pt[hit], side="right") - 1, 0, B - 1)
            np.add.at(sens, (aidx[hit], b, ci[hit]), w)
        # move backwards one hour
        uu = _interp(u, lons, lats, times, plon, plat, pt) + rng.normal(0, TURB_MS, plon.shape)
        vv = _interp(v, lons, lats, times, plon, plat, pt) + rng.normal(0, TURB_MS, plon.shape)
        rr = _interp(rain_per_h, lons, lats, times, plon, plat, pt)
        cumrain += np.maximum(rr, 0)
        plon = plon - uu * 3600.0 / (M_PER_DEG_LON * np.cos(np.radians(plat)))
        plat = plat - vv * 3600.0 / M_PER_DEG_LAT
        pt = pt - 1.0
        alive &= (pt >= times[0]) & (plon > lons[0]) & (plon < lons[-1]) & (plat > lats[0]) & (plat < lats[-1])
    return sens, paths


def make_fire_cells(fires, grid_deg, west, south, east, north):
    """Aggregate fire detections (lon, lat, frp, hours_ago) into grid cells.

    Returns cell structure with per-cell FRP for the last 24 h (d0) and the 24 h before (d1).
    """
    nx = int(round((east - west) / grid_deg))
    ny = int(round((north - south) / grid_deg))
    lookup = -np.ones((ny, nx), dtype=int)
    cells = []
    for lon, lat, frp, hours_ago in fires:
        ix = int((lon - west) // grid_deg)
        iy = int((lat - south) // grid_deg)
        if not (0 <= ix < nx and 0 <= iy < ny):
            continue
        k = lookup[iy, ix]
        if k < 0:
            k = len(cells)
            lookup[iy, ix] = k
            cells.append({"lon": west + (ix + 0.5) * grid_deg, "lat": south + (iy + 0.5) * grid_deg,
                          "frp0": 0.0, "frp1": 0.0, "n0": 0, "n1": 0})
        c = cells[k]
        if hours_ago < 24:
            c["frp0"] += frp
            c["n0"] += 1
        elif hours_ago < 48:
            c["frp1"] += frp
            c["n1"] += 1
    from config import region_index
    if cells:
        reg = region_index([c["lon"] for c in cells], [c["lat"] for c in cells])
        for c, r in zip(cells, reg):
            c["region"] = int(r)
    return {"grid": {"lon0": west, "lat0": south, "d": grid_deg, "nx": nx, "ny": ny},
            "lookup": lookup, "n": len(cells), "cells": cells}


def footprints(weather, cells, seed=1, path_arrivals=None, log=print):
    """Run all members. Returns S[M, A, R, B] (FRP-weighted, per region) and paths."""
    M = len(weather["members"])
    A, B, R = len(ARRIVALS_H), len(EMIS_BINS_H), len(REGIONS)
    if path_arrivals is None:
        from config import PATH_ARRIVALS_H
        path_arrivals = [h for h in PATH_ARRIVALS_H if h in ARRIVALS_H]
    S = np.zeros((M, A, R, B), dtype=np.float32)
    paths = []
    rng = np.random.default_rng(seed)
    cl = cells["cells"]
    # FRP weights per cell: the most recent day if burning, otherwise the day before
    frp_w = np.array([c["frp0"] if c["frp0"] > 0 else c["frp1"] for c in cl], dtype=float)
    reg = np.array([c.get("region", 0) for c in cl], dtype=int)
    for m in range(M):
        if cells["n"] == 0:
            sens = np.zeros((A, B, 0))
            _, p = trace_member(weather["u"][m], weather["v"][m], weather["rain"][m], weather["lons"],
                                weather["lats"], weather["times"], cells, rng, path_arrivals)
        else:
            sens, p = trace_member(weather["u"][m], weather["v"][m], weather["rain"][m], weather["lons"],
                                   weather["lats"], weather["times"], cells, rng, path_arrivals)
            # FRP-weighted sum per region: S_r = sum_c sens_c * frp_c  (units: hours * MW, scaled later)
            weighted = sens * frp_w[None, None, :]
            for r in range(R):
                mask = reg == r
                if mask.any():
                    S[m, :, r, :] = weighted[:, :, mask].sum(axis=2)
        paths.append([p[a] for a in path_arrivals])
        if log and (m % 8 == 0 or m == M - 1):
            log(f"  traced member {m + 1}/{M}")
    return S, paths, path_arrivals
