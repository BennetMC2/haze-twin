#!/usr/bin/env bash
# Checks which historical archives are reachable from GitHub Actions. Output goes to history/probe.txt
out=history/probe.txt; : > $out
p(){ echo "== $1" >> $out; curl -s -m 40 "$2" | head -c ${3:-1500} >> $out; echo -e "\n" >> $out; }
p "GEFS pds 2019-09-15 00z listing" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.20190915/00/&delimiter=/"
p "GEFS pds 2019 pgrb2ap5 listing" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.20190915/00/pgrb2ap5/gep01&max-keys=5" 2500
p "GEFS pds 2019 pgrb2a listing" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.20190915/00/pgrb2a/gep01&max-keys=5" 2500
p "GEFS pds 2023-09-20 00z listing" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.20230920/00/atmos/&delimiter=/"
p "GEFS pds 2023 pgrb2ap5" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.20230920/00/atmos/pgrb2ap5/gep01&max-keys=5" 2500
p "GEFS pds 2015 listing" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.2015&max-keys=3"
p "GEFS pds earliest" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&max-keys=3"
p "NEA psi date 2019-09-15" "https://api-open.data.gov.sg/v2/real-time/api/psi?date=2019-09-15" 2500
p "NEA pm25 date 2019-09-15" "https://api-open.data.gov.sg/v2/real-time/api/pm25?date=2019-09-15" 1500
p "NEA psi date 2015-09-25" "https://api-open.data.gov.sg/v2/real-time/api/psi?date=2015-09-25" 800
p "NEA v1 psi 2015-09-25" "https://api.data.gov.sg/v1/environment/psi?date=2015-09-25" 800
p "FIRMS availability" "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/${FIRMS_MAP_KEY}/all" 3000
p "FIRMS SP 2019-09-15" "https://firms.modaps.eosdis.nasa.gov/api/area/csv/${FIRMS_MAP_KEY}/VIIRS_SNPP_SP/95,-8,120,6/1/2019-09-15" 600
p "Open-Meteo archive" "https://archive-api.open-meteo.com/v1/archive?latitude=-2&longitude=113&start_date=2019-08-01&end_date=2019-08-03&daily=precipitation_sum" 600
p "GEFS idx sample 2019" "https://noaa-gefs-pds.s3.amazonaws.com/gefs.20190915/00/pgrb2ap5/gep01.t00z.pgrb2a.0p50.f024.idx" 3000
p "GEFS idx sample 2023" "https://noaa-gefs-pds.s3.amazonaws.com/gefs.20230920/00/atmos/pgrb2ap5/gep01.t00z.pgrb2a.0p50.f024.idx" 3000
