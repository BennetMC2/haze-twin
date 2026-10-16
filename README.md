# Haze Twin

Odds for Singapore's haze over the next six days, from a digital twin of Southeast Asia's fires, smoke and winds.

Every six hours the twin takes in the latest readings, traces the air arriving in Singapore back through 31 weather forecasts, and runs 100,000 possible futures. It shows the result as plain odds ("38% chance PSI is 100 or below by Thursday"), as storylines (clears early, clears later, stays unhealthy, gets worse), and on a live map of fires, smoke, wind and the paths the air took.

This is an independent, public-good project. For health advice, follow NEA.

## How it works

1. **Measured** — NEA 24-hour PSI and PM2.5 for each Singapore region (data.gov.sg), and fire hotspots from NASA FIRMS (VIIRS on NOAA-20 and NOAA-21).
2. **Weather** — the NOAA GEFS ensemble: 31 forecasts of wind and rain at 0.5°, out to six days, cropped to 92–124°E, 10°S–10°N.
3. **Back-tracing** — for each forecast, 300 particles are released at Singapore every six hours and traced back four days through that forecast's winds (mean of 925 and 850 hPa) with turbulent spread. Where they pass over fire cells they pick up sensitivity to those fires, reduced by rain along the way and by age (`pipeline/trajectories.py`).
4. **Futures** — the engine samples the uncertain parts on top of each forecast: smoke per unit of fire power, how long fires keep burning, how much rain puts them out, new fires, and background pollution. 100,000 futures in all (`engine/engine.js`).
5. **Anchoring** — futures are weighted by how well they match NEA's readings over the last day, so the twin starts from the real air.
6. **Storylines and odds** — futures are grouped by outcome; the odds are the share in each. Each storyline says what distinguishes it (rain, wind, fires).
7. **Record** — every forecast is logged in `history/` and scored two days later against what NEA measured. The page shows hits, misses and skill against "same as today".

The page runs the same engine in the browser, so **Rerun** repeats the 100,000 futures on your device with NEA's latest reading.

## Layout

```
pipeline/   Python: fetch NEA, FIRMS and GEFS; back-trace; write site/data/inputs.json
engine/     JavaScript: Monte Carlo, storylines, record (engine.js runs in Node and the browser)
site/       The web page (GitHub Pages)
history/    Append-only log of issued forecasts and observations (committed by the workflow)
.github/workflows/twin.yml   Runs every 6 hours, archives each run as a release asset, deploys the page
```

## Run it locally

```
pip install -r pipeline/requirements.txt
python pipeline/build_inputs.py --sample      # synthetic data, no network or keys
node engine/run.js --no-history
cp engine/engine.js site/engine.js
python -m http.server -d site 8000            # open http://localhost:8000
```

For live data: set `FIRMS_MAP_KEY` (free from NASA FIRMS) and run `python pipeline/build_inputs.py` without `--sample`. It downloads about 800 small GEFS files from NOMADS and takes 10–20 minutes.

## Setup on GitHub

1. Add the repository secret `FIRMS_MAP_KEY`. Optional: `DATAGOVSG_API_KEY` for higher data.gov.sg limits.
2. Settings → Pages → Source: GitHub Actions.
3. Actions → Run the twin → Run workflow (or wait for the schedule).

## Limits and known gaps

- Smoke amounts are scaled to match NEA readings, not measured at the fires. Peat smoke per unit of fire power varies two- to three-fold between studies; the twin samples that range.
- PSI is computed from 24-hour PM2.5 with NEA's breakpoints (12, 55, 150, 250, 350 and 500 µg/m³). PSI also counts other pollutants, which matter little during haze.
- One weather ensemble (GEFS) for now. ECMWF IFS and AIFS ensembles are the next addition.
- Satellites miss fires under thick cloud and smoke. Suomi-NPP stops on 1–2 November 2026 and MODIS in 2027; the twin uses NOAA-20 and NOAA-21.
- NEA's regional ratios are carried forward and fade over two days.

## Data and credits

- PSI and PM2.5: National Environment Agency, via data.gov.sg (Singapore Open Data Licence).
- Fire hotspots: NASA FIRMS, LANCE/EOSDIS, VIIRS 375 m NRT active fire products.
- Weather: NOAA NCEP Global Ensemble Forecast System (GEFS), via NOMADS.
- Coastlines: Natural Earth (public domain), via world-atlas.

Code: MIT licence.
