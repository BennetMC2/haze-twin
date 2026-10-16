"""Synthetic inputs for testing the pipeline and the page without network access.

The weather is a simple south-easterly flow south of the equator turning south-westerly
further north, with member-to-member differences in wind turning and rain timing.
Everything produced here is labelled as sample data downstream.
"""
from datetime import datetime, timedelta, timezone

import numpy as np

from config import WEST, EAST, SOUTH, NORTH, GEFS_MEMBERS


def weather(t0, seed=3):
    rng = np.random.default_rng(seed)
    lons = np.arange(WEST, EAST + 0.001, 0.5)
    lats = np.arange(SOUTH, NORTH + 0.001, 0.5)
    times = np.arange(-96.0, 390.1, 6.0)
    LON, LAT = np.meshgrid(lons, lats)
    M = len(GEFS_MEMBERS)
    U = np.zeros((M, len(times), len(lats), len(lons)), np.float32)
    V = np.zeros_like(U)
    R = np.zeros_like(U)
    for m in range(M):
        turn_t = rng.normal(70, 30)          # hours: when the north-east monsoon pushes south
        turn_amp = rng.uniform(0, 1) ** 2      # how strongly
        rain_t = rng.normal(65, 25)          # rain onset over Kalimantan
        rain_amp = rng.uniform(0, 14)
        sw_t = rng.normal(45, 15)            # south-westerly burst over south Sumatra
        sw_amp = max(0.0, rng.normal(0.2, 0.35))
        monsoon_t = rng.normal(300, 60)      # sustained rains, hours ahead
        monsoon_amp = rng.uniform(1.5, 4.0)
        for ti, t in enumerate(times):
            w = np.clip((LAT - 1.5) / 3.0, 0, 1)
            u = -7.0 * (1 - w) + 5.0 * w
            v = 2.8 * (1 - w) + 4.5 * w
            u = u + 2.0 * np.sin(LAT * 0.9 + t / 20.0 + m) + rng.normal(0, 0.3)
            v = v + 1.6 * np.cos(LON * 0.5 - t / 17.0 + m * 0.7)
            if t > 0:
                k = turn_amp / (1 + np.exp(-(t - turn_t) / 10.0)) * np.clip((LAT + 3) / 5, 0, 1)
                u = u * (1 - k) + (-6.0) * k
                v = v * (1 - k) + (-6.5) * k
                ks = sw_amp * np.exp(-((t - sw_t) / 18.0) ** 2) * np.exp(-(((LON - 104.5) / 4) ** 2 + ((LAT + 2) / 3) ** 2))
                u = u * (1 - ks) + 2.0 * ks
                v = v * (1 - ks) + 6.0 * ks
            U[m, ti] = u
            V[m, ti] = v
            base = 0.3 * np.clip(LAT + 2, 0, None)
            if t > 0:
                kal = np.exp(-(((LON - 113.5) / 3.0) ** 2 + ((LAT + 1.5) / 2.5) ** 2))
                sum_ = np.exp(-(((LON - 104.0) / 3.0) ** 2 + ((LAT + 2.5) / 2.5) ** 2))
                base = base + rain_amp * kal / (1 + np.exp(-(t - rain_t) / 6.0)) * np.exp(-max(0, t - rain_t - 36) / 30.0)
                base = base + monsoon_amp * (kal + sum_) / (1 + np.exp(-(t - monsoon_t) / 12.0))
            R[m, ti] = np.maximum(0, base + rng.normal(0, 0.3, base.shape))
    return {"members": GEFS_MEMBERS, "lons": lons, "lats": lats, "times": times, "u": U, "v": V, "rain": R}


CLUSTERS = [(113.6, -2.3, 220, 0.6), (115.0, -3.05, 90, 0.32), (104.9, -3.2, 150, 0.45),
            (103.6, -1.55, 40, 0.3), (110.3, -0.5, 30, 0.38), (101.9, 0.7, 5, 0.25)]


def fires(seed=5):
    rng = np.random.default_rng(seed)
    dets = []
    for lon, lat, n, sp in CLUSTERS:
        for _ in range(n):
            for day in (0, 1):
                if rng.uniform() < 0.8:
                    dets.append({"lon": float(lon + rng.normal(0, sp)), "lat": float(lat + rng.normal(0, sp * 0.7)),
                                 "frp": float(rng.lognormal(2.6, 0.8)), "h": float(rng.uniform(0, 24) + 24 * day),
                                 "sat": "N21" if rng.uniform() < 0.5 else "N20", "conf": "n"})
    return dets, {"N21": "sample", "N20": "sample"}


def nea(t0):
    """Hourly history for the last 7 days resembling mid-October 2026."""
    regions = {"north": 0.92, "south": 0.97, "east": 0.94, "west": 1.06, "central": 1.0}
    psi_hist, pm24_hist, pm1_hist = [], [], []
    rng = np.random.default_rng(11)
    for h in range(-168, 1):
        t = t0 + timedelta(hours=h)
        d = h / 24.0
        pm24 = 70 + 55 * np.exp(-((d + 1.6) / 1.2) ** 2) + 25 * (d > -4) + rng.normal(0, 2)
        pm1 = pm24 + rng.normal(0, 18)
        def psi_of(pm):
            bp = [(0, 0), (12, 50), (55, 100), (150, 200), (250, 300), (350, 400), (500, 500)]
            for (c0, i0), (c1, i1) in zip(bp, bp[1:]):
                if pm <= c1:
                    return round(i0 + (pm - c0) * (i1 - i0) / (c1 - c0))
            return 500
        row_psi = {"t": t.isoformat()}
        row_pm24 = {"t": t.isoformat()}
        row_pm1 = {"t": t.isoformat()}
        for r, f in regions.items():
            row_psi[r] = psi_of(pm24 * f)
            row_pm24[r] = round(pm24 * f)
            row_pm1[r] = round(max(5, pm1 * f))
        for row in (row_psi, row_pm24, row_pm1):
            row["national"] = max(v for k, v in row.items() if k != "t")
        psi_hist.append(row_psi)
        pm24_hist.append(row_pm24)
        pm1_hist.append(row_pm1)
    return {"psi_hist": psi_hist, "pm24_hist": pm24_hist, "pm1_hist": pm1_hist, "latest_time": psi_hist[-1]["t"]}
