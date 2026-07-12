"""
fetch_dc.py

Pulls DC hate/bias crime data from the public ArcGIS REST endpoint (MPD Bias Crime layer).
Source confirmed live in a prior session:
  https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/MapServer/7/query

Confirmed field names (from live layer metadata):
  CCN, ADDRESS, DATE_OF_OFFENSE, POLICE_DISTRICT, WARD,
  TYPE_OF_HATE_BIAS, TARGETED_GROUP, TOP_OFFENSE_TYPE,
  MAR_LATITUDE, MAR_LONGITUDE

Pulls with outFields=* (not a narrowed list) so TARGETED_GROUP's full raw
granularity (e.g. "Israeli", "Chinese", "Korean", "African") is preserved
for the Jewish/Asian subgroup breakdowns computed later in analyze.py.

NOTE: this domain (maps2.dcgis.dc.gov) is not in this sandbox's network
allowlist, so this script is written against confirmed live field names
and layer metadata but could not be dry-run inside this environment.
It will run fine in the GitHub Actions runner (unrestricted egress).

Output: writes data/raw/dc_raw.json (list of feature attribute dicts)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse

DC_ENDPOINT = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/MapServer/7/query"
OUT_PATH = os.path.join("data", "raw", "dc_raw.json")
PAGE_SIZE = 1000
MAX_RETRIES = 3


def fetch_page(offset):
    params = {
        "where": "1=1",
        "outFields": "*",
        "outSR": "4326",  # request lat/lon in WGS84 rather than Web Mercator
        "f": "json",
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE_SIZE),
        "orderByFields": "DATE_OF_OFFENSE ASC",
    }
    url = DC_ENDPOINT + "?" + urllib.parse.urlencode(params)

    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dc-montco-hate-crime-pipeline/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            data = json.loads(body)
            if "error" in data:
                raise RuntimeError(f"ArcGIS error: {data['error']}")
            return data
        except Exception as e:
            last_err = e
            print(f"  fetch_page(offset={offset}) attempt {attempt+1} failed: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch offset={offset} after {MAX_RETRIES} attempts: {last_err}")


def fetch_all():
    all_features = []
    offset = 0
    while True:
        print(f"Fetching DC records at offset {offset}...")
        data = fetch_page(offset)
        features = data.get("features", [])
        if not features:
            break
        all_features.extend(f["attributes"] for f in features)
        if len(features) < PAGE_SIZE:
            # last page
            break
        offset += PAGE_SIZE
    return all_features


def main():
    records = fetch_all()
    print(f"Fetched {len(records)} DC records total.")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(records, f, indent=None)

    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
