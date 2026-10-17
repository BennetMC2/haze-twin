# Improving the physics: roadmap

Each step only counts once it beats the current model on the replayed seasons (2014, 2015, 2019, 2023, held out one at a time) and on the live record. Scores to compare: typical miss against "same as today", CRPS, coverage of the 90% range, and reliability of the yes/no odds.

1. **Smoke height and mixing.** Today every parcel uses one wind (the mean of 925 and 850 hPa). Instead, trace at three heights and weight them by GEFS/ECMWF boundary-layer depth, which differs between day and night. This tests whether smoke that rises above the mixed layer explains the days when the model runs too high.
2. **Fire emissions.** De-duplicate FRP per cell and per satellite overpass rather than dividing by the number of satellites. Integrate fire radiative energy over the day. Use the GFAS emissions (CAMS) as an alternative input. Before NOAA-20/21 take over from SNPP, check that the satellite handover does not shift the inputs.
3. **Peat versus surface fires.** Use a peat map (for example the Global Peatland Database) to give peat cells longer burning and more smoke per unit of FRP. This replaces the single 4–40 day half-life range.
4. **Rain removal.** Calibrate `WET_LAMBDA` and the 1.5 mm/6 h threshold against replay errors on rainy days, instead of using hand-set values.
5. **Benchmark against a dispersion model.** On a few replay days, compare the twin's air-path footprints with HYSPLIT (NOAA READY) or FLEXPART run on the same GEFS winds. Large disagreements show where the simplified transport fails.
6. **Second opinions.** CAMS is now scored daily against the twin and blended in only when it does better (`tools/learn.py`). Add NAAPS and the ECMWF IFS ensemble the same way once their downloads fit within the run time.
7. **Season-end estimate.** Replace the fixed onset dates and El Niño/IOD shifts with a fit to BMKG rainfall-onset records and past haze end dates (`tools/season_backtest.py` reports the current error).
