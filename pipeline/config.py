"""Shared settings for the Haze Twin pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DATA = ROOT / "site" / "data"
HISTORY = ROOT / "history"
WORK = ROOT / ".work"          # scratch downloads, not committed

# Domain for weather, fires and trajectories (degrees)
WEST, EAST, SOUTH, NORTH = 92.0, 124.0, -10.0, 10.0

# Receptor: Singapore
SG_LON, SG_LAT = 103.82, 1.35

# Arrival times (hours relative to t0 = latest NEA reading) for back-trajectories
ARRIVALS_H = list(range(-18, 139, 6)) + list(range(150, 373, 12))   # 6-hourly to 138 h, then 12-hourly to 366 h
# Emission-time bins (hours relative to t0), 24 h wide, start values
EMIS_BINS_H = list(range(-96, 384, 24))         # -96 .. 360, 20 bins (last ends at 384)
BACK_HOURS = 96                                 # how far back each particle is traced
PARTICLES = 300                                 # per member per arrival
TURB_MS = 2.0                                   # random-walk wind perturbation (m/s)
WET_LAMBDA = 0.08                               # wet removal per mm of rain along the path
AGE_EFOLD_H = 96.0                              # loss of smoke with age (dry deposition, mixing out)
FIRE_GRID_DEG = 0.5                             # fire cells

# Weather ensemble (NOAA GEFS, 0.5 degree, from NOMADS grib filter)
GEFS_MEMBERS = ["c00"] + [f"p{i:02d}" for i in range(1, 31)]
GEFS_STEPS = list(range(0, 145, 6)) + list(range(156, 385, 12))   # 45 steps out to 16 days
GEFS_MAX_H = 384
GEFS_HISTORY_CYCLES = 16                        # past cycles of control analyses (4 days)

# Wind grid shipped to the browser for the map (coarse, int8-quantised)
WIND_DISPLAY_DEG = 2.0
WIND_SCALE = 0.25                               # m/s per int8 unit

# Paths drawn on the map: every 12 h for 6 days, then every 24 h
PATH_ARRIVALS_H = [h for h in range(0, 145, 12)] + [h for h in range(150, 373, 24)]

# Source regions used for attribution and fire evolution
REGIONS = [
    {"id": "aceh",   "name": "Aceh",               "island": "Sumatra",   "lon": 96.8,  "lat": 4.4},
    {"id": "nsum",   "name": "North Sumatra",      "island": "Sumatra",   "lon": 99.0,  "lat": 2.4},
    {"id": "riau",   "name": "Riau",               "island": "Sumatra",   "lon": 101.7, "lat": 0.5},
    {"id": "wsum",   "name": "West Sumatra",       "island": "Sumatra",   "lon": 100.4, "lat": -0.9},
    {"id": "jambi",  "name": "Jambi",              "island": "Sumatra",   "lon": 103.3, "lat": -1.7},
    {"id": "ssum",   "name": "South Sumatra",      "island": "Sumatra",   "lon": 104.4, "lat": -3.3},
    {"id": "lamp",   "name": "Lampung",            "island": "Sumatra",   "lon": 105.0, "lat": -5.0},
    {"id": "wkal",   "name": "West Kalimantan",    "island": "Kalimantan", "lon": 110.5, "lat": -0.2},
    {"id": "ckal",   "name": "Central Kalimantan", "island": "Kalimantan", "lon": 113.3, "lat": -1.8},
    {"id": "skal",   "name": "South Kalimantan",   "island": "Kalimantan", "lon": 115.3, "lat": -3.0},
    {"id": "ekal",   "name": "East Kalimantan",    "island": "Kalimantan", "lon": 116.6, "lat": 0.4},
    {"id": "pmy",    "name": "Peninsular Malaysia", "island": "Malaysia",  "lon": 102.2, "lat": 3.9},
    {"id": "borneo_n", "name": "Sarawak and Sabah", "island": "Malaysia",  "lon": 113.6, "lat": 3.0},
    {"id": "java",   "name": "Java",               "island": "Java",      "lon": 110.0, "lat": -7.2},
]

# Home cities: the same fires and weather, traced back from each city (GEFS members only, to keep runs short).
# Readings come from the World Air Quality Index Project's nearest station (US AQI, converted to PM2.5).
CITIES = [
    {"id": "kl", "name": "Kuala Lumpur", "country": "Malaysia", "lon": 101.69, "lat": 3.14},
    {"id": "penang", "name": "Penang", "country": "Malaysia", "lon": 100.33, "lat": 5.41},
    {"id": "jb", "name": "Johor Bahru", "country": "Malaysia", "lon": 103.76, "lat": 1.49},
    # Jakarta left out: its air is mostly local city pollution, which the twin does not model.
]

# NEA regions
NEA_REGIONS = ["national", "north", "south", "east", "west", "central"]

USER_AGENT = "haze-twin/0.1 (public-good haze forecast; github.com/BennetMC2/haze-twin)"


def region_index(lon, lat):
    """Nearest-centroid region index for arrays of lon/lat (numpy) or scalars."""
    import numpy as np
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    best = np.full(lon.shape, -1, dtype=int)
    bestd = np.full(lon.shape, np.inf)
    for i, r in enumerate(REGIONS):
        d = (lon - r["lon"]) ** 2 + (lat - r["lat"]) ** 2
        m = d < bestd
        best[m] = i
        bestd[m] = d[m]
    return best
