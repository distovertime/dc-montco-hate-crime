"""
fetch_mcpd.py

Pulls and joins two Montgomery County (DataMontgomery / Socrata) datasets:

  1. MCPD Bias Incidents      resource id: 7bhj-887p
     https://data.montgomerycountymd.gov/resource/7bhj-887p.json
     Fields include: id (join key, matches Crime dataset's case_number),
     incident_date, district, bias_code, bias_code_2, bias, status,
     victim/suspect counts. NO lat/lon here.

  2. Crime (all crimes)       resource id: icn6-v9z3
     https://data.montgomerycountymd.gov/resource/icn6-v9z3.json
     Fields include: case_number (join key), latitude, longitude,
     address_number, address_street, street_type, district, dispatch date, etc.
     Confirmed live via web_fetch in a prior session.

Join: bias.id == crime.case_number

This domain IS reachable via web_fetch in prior sessions but bash_tool's
urllib calls were blocked by the sandbox network allowlist when tested
previously. The logic below matches the confirmed live JSON field names
and pagination pattern. It will run in the GitHub Actions runner.

Output: writes data/raw/mcpd_raw.json (list of joined incident dicts)
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse

BIAS_ENDPOINT = "https://data.montgomerycountymd.gov/resource/7bhj-887p.json"
CRIME_ENDPOINT = "https://data.montgomerycountymd.gov/resource/icn6-v9z3.json"

OUT_PATH = os.path.join("data", "raw", "mcpd_raw.json")
PAGE_SIZE = 1000
MAX_RETRIES = 3


def fetch_socrata(endpoint, order_field=None):
    all_rows = []
    offset = 0
    while True:
        params = {"$limit": str(PAGE_SIZE), "$offset": str(offset)}
        if order_field:
            params["$order"] = order_field
        url = endpoint + "?" + urllib.parse.urlencode(params)

        last_err = None
        rows = None
        for attempt in range(MAX_RETRIES):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "dc-montco-hate-crime-pipeline/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read()
                rows = json.loads(body)
                break
            except Exception as e:
                last_err = e
                print(f"  fetch_socrata({endpoint}, offset={offset}) attempt {attempt+1} failed: {e}", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
        if rows is None:
            raise RuntimeError(f"Failed to fetch {endpoint} offset={offset}: {last_err}")

        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows


def main():
    print("Fetching MCPD Bias Incidents (7bhj-887p)...")
    bias_rows = fetch_socrata(BIAS_ENDPOINT, order_field="incident_date")
    print(f"  {len(bias_rows)} bias incident records.")

    print("Fetching MCPD Crime dataset (icn6-v9z3)...")
    crime_rows = fetch_socrata(CRIME_ENDPOINT, order_field="start_date")
    print(f"  {len(crime_rows)} crime records.")

    # Build a lookup on case_number -> crime record.
    # Multiple crime rows can share a case_number (rare); keep the first with lat/lon.
    crime_by_case = {}
    for row in crime_rows:
        case_num = row.get("case_number")
        if not case_num:
            continue
        if case_num not in crime_by_case or (
            "latitude" not in crime_by_case[case_num] and "latitude" in row
        ):
            crime_by_case[case_num] = row

    joined = []
    unmatched = 0
    for bias in bias_rows:
        join_key = bias.get("id")
        crime = crime_by_case.get(join_key)
        if crime is None:
            unmatched += 1
            # keep the bias row even without lat/lon; build_data.py can
            # decide whether to drop geocoding-less rows.
            merged = dict(bias)
            merged["_crime_match"] = False
        else:
            merged = dict(bias)
            merged["latitude"] = crime.get("latitude")
            merged["longitude"] = crime.get("longitude")
            merged["address_number"] = crime.get("address_number")
            merged["address_street"] = crime.get("address_street")
            merged["street_type"] = crime.get("street_type")
            merged["crime_district"] = crime.get("district")
            merged["_crime_match"] = True
        joined.append(merged)

    print(f"Joined {len(joined)} records ({unmatched} unmatched against Crime dataset).")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(joined, f, indent=None)

    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
