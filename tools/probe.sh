#!/usr/bin/env bash
out=history/probe.txt; : > $out
p(){ echo "== $1" >> $out; curl -s -m 60 "$2" | head -c ${3:-1500} >> $out; echo -e "\n" >> $out; }
B=https://noaa-gefs-retrospective.s3.amazonaws.com
p "reforecast root" "$B/?list-type=2&delimiter=/&prefix=GEFSv12/reforecast/2015/" 1500
p "reforecast day" "$B/?list-type=2&delimiter=/&prefix=GEFSv12/reforecast/2015/2015092100/" 1500
p "reforecast c00 d1-10" "$B/?list-type=2&prefix=GEFSv12/reforecast/2015/2015092100/c00/Days%3A1-10/&max-keys=60" 9000
p "reforecast c00 d10-16" "$B/?list-type=2&prefix=GEFSv12/reforecast/2015/2015092100/c00/Days%3A10-16/&max-keys=60" 6000
p "reforecast idx ugrd" "$B/GEFSv12/reforecast/2015/2015092100/c00/Days%3A1-10/ugrd_pres_2015092100_c00.grib2.idx" 2500
p "reforecast idx apcp" "$B/GEFSv12/reforecast/2015/2015092100/c00/Days%3A1-10/apcp_sfc_2015092100_c00.grib2.idx" 1500
p "NEA pm25 2015-09-25" "https://api-open.data.gov.sg/v2/real-time/api/pm25?date=2015-09-25" 600
p "NEA pm25 2016-01-05" "https://api-open.data.gov.sg/v2/real-time/api/pm25?date=2016-03-05" 400
p "NEA psi 2023-10-05" "https://api-open.data.gov.sg/v2/real-time/api/psi?date=2023-10-05" 400
p "GEFS 2019 pgrb2a idx f024" "https://noaa-gefs-pds.s3.amazonaws.com/gefs.20190915/00/pgrb2a/gep01.t00z.pgrb2af24.idx" 4000
p "GEFS 2019 pgrb2a list" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&prefix=gefs.20190915/00/pgrb2a/gep01.t00z.pgrb2af2&max-keys=8" 3000
p "GEFS 2019 pgrb2ap5?" "https://noaa-gefs-pds.s3.amazonaws.com/?list-type=2&delimiter=/&prefix=gefs.20190915/" 1000
