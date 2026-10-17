# Haze Twin

Odds for Singapore's haze over the next 15 days, and an estimate of when the haze season will end. Built on a computer model (a "digital twin") of the fires, smoke and winds across Southeast Asia.

Every six hours the full model runs: it fetches new NEA, fire and weather data, traces the air arriving in Singapore back through each weather forecast (31 from NOAA GEFS, plus 25 forecasts from ECMWF's AI weather model, AIFS, when its download succeeds), and simulates 100,000 futures. Every hour in between, the 100,000 futures are rerun with NEA's latest reading on the latest weather and fire data. The page shows the result as odds ("38% chance PSI is 100 or below by Thursday"), as storylines (clears early, clears later, stays unhealthy, gets worse), and on a map of fires, smoke, wind and the paths the air took.

Experimental and independent. **Not an official forecast.** For health advice, follow NEA ([haze.gov.sg](https://www.haze.gov.sg/)). The odds have not been validated for extreme haze (PSI above 300).

## How it works

1. **Measured:** NEA 24-hour PSI and PM2.5 for each Singapore region (data.gov.sg), and fire hotspots from NASA FIRMS (VIIRS on NOAA-20 and NOAA-21).
2. **Weather:** the NOAA GEFS ensemble (31 forecasts of wind and rain at 0.5°, out to 16 days, cropped to 92–124°E, 10°S–10°N), plus 25 of the 50 forecasts from ECMWF's AI ensemble (AIFS ENS) when it downloads. Beyond the end of the ECMWF data, each ECMWF forecast is continued by a GEFS forecast. The pipeline can also use the ECMWF IFS ensemble, but it is switched off in the live runs.
   Other free sources: airport visibility near the fires (METAR), the CAMS smoke model as a second opinion (via Open-Meteo), news (GDELT), satellite imagery (NASA GIBS), El Niño (NOAA RONI) and the Indian Ocean Dipole (NOAA DMI).
3. **Back-tracing:** for each forecast, 300 particles are released at Singapore every six hours and traced back four days through that forecast's winds (mean of 925 and 850 hPa) with turbulent spread. Where they pass over fire cells they pick up sensitivity to those fires, reduced by rain along the way and by age (`pipeline/trajectories.py`).
4. **Futures:** the engine samples the uncertain parts on top of each forecast: smoke per unit of fire power, how long fires keep burning, how much rain puts them out, new fires, and background pollution. 100,000 futures in all (`engine/engine.js`).
5. **Anchoring:** futures are weighted by how well they match NEA's readings over the last day, so the simulation starts from current conditions.
6. **Storylines and odds:** futures are grouped by outcome; the odds are the share in each. Each storyline says what distinguishes it (rain, wind, fires).
7. **Horizons:** days 1–6 are the full twin with storylines; days 7–15 run on the longer forecasts and are shown as chance by date; the season outlook (experimental) estimates when rain sets in over the fire areas, from the forecasts where they reach it and from normal onset dates shifted by El Niño and the IOD beyond that.
8. **Record:** every forecast is logged in `history/` and scored two days later against what NEA measured. The page shows hits, misses and skill against "same as today". Each day at 1:30 pm Singapore time, a scoring job reviews the misses and applies a small, capped correction (`tools/learn.py`).

The page runs the same engine in the browser, so **Rerun** repeats the 100,000 futures on your device with NEA's latest reading.

## Layout

```
pipeline/   Python: fetch NEA, FIRMS and GEFS; back-trace; write site/data/inputs.json
engine/     JavaScript: Monte Carlo, storylines, record (engine.js runs in Node and the browser)
site/       The web page (GitHub Pages)
history/    Append-only log of issued forecasts and observations (committed by the workflow)
.github/workflows/twin.yml   Full model run every 6 hours, archives each run as a release asset, deploys the page
.github/workflows/refresh.yml  Hourly rerun of the 100,000 futures with NEA's latest readings
.github/workflows/learn.yml    Daily scoring and capped correction, 1:30 pm Singapore time
relay/      Cloudflare Worker for "Ask about the haze" (see relay/README.md)
```

## Run it locally

```
pip install -r pipeline/requirements.txt
python pipeline/build_inputs.py --sample      # synthetic data, no network or keys
node engine/run.js --no-history
cp engine/engine.js site/engine.js
python -m http.server -d site 8000            # open http://localhost:8000
```

For live data: set `FIRMS_MAP_KEY` (free from NASA FIRMS) and run `python pipeline/build_inputs.py` without `--sample`. It downloads many small GEFS files from NOMADS and takes 25–40 minutes. Local runs also fetch both ECMWF ensembles (AIFS and IFS) by default; set `ECMWF_AIFS=0` and `ECMWF_ENS=0` to skip them, or `ECMWF_MEMBERS` to change how many forecasts each one adds (default 25).

## Setup on GitHub

1. Add the repository secret `FIRMS_MAP_KEY`. Optional: `DATAGOVSG_API_KEY` for higher data.gov.sg limits.
2. Settings → Pages → Source: GitHub Actions.
3. Actions → Run the twin → Run workflow (or wait for the schedule).

## Limits and known gaps

- Smoke amounts are scaled to match NEA readings, not measured at the fires. Peat smoke per unit of fire power varies two- to three-fold between studies; the twin samples that range.
- PSI is computed from 24-hour PM2.5 with NEA's breakpoints (12, 55, 150, 250, 350 and 500 µg/m³). PSI also counts other pollutants, which matter little during haze.
- ECMWF's AI ensemble (AIFS ENS) was added in October 2026 and has not yet been through the past-season replays. If its download fails, the run uses GEFS alone.
- In replays of 2015, 2019 and 2023, the best guess 1 to 3 days ahead was no better than "same as today", and "above 300" odds ran too high.
- Satellites miss fires under thick cloud and smoke. Suomi-NPP stops on 1–2 November 2026 and MODIS in 2027; the twin uses NOAA-20 and NOAA-21.
- NEA's regional ratios are carried forward and fade over two days.

## Data and credits

- PSI and PM2.5: National Environment Agency, via data.gov.sg (Singapore Open Data Licence).
- Fire hotspots: NASA FIRMS, LANCE/EOSDIS, VIIRS 375 m NRT active fire products.
- Weather: NOAA NCEP Global Ensemble Forecast System (GEFS), via NOMADS; ECMWF AIFS ensemble, via [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data) (CC BY 4.0).
- Smoke model (second opinion): Copernicus Atmosphere Monitoring Service (CAMS), via Open-Meteo.
- News signals: The GDELT Project.
- Air quality stations in the region: OpenAQ.
- El Niño and Indian Ocean Dipole: NOAA (RONI and DMI indices).
- Singapore Central AQI: AQICN, the World Air Quality Index Project (waqi.info).
- Coastlines: Natural Earth (public domain), via world-atlas.

Code: MIT licence.
