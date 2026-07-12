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
     This table covers "2000 to present" county-wide crime and is very
     large (all crime types, not just bias incidents) - pulling it in full
     is slow and wasteful. Instead, once we have the bias incidents' case
     numbers, we query the Crime dataset with a batched
     `$where=case_number in (...)` filter so we only ever fetch the rows
     we actually need to join against.

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
CASE_NUMBER_BATCH_SIZE = 50  # keep query URLs a safe length


def fetch_url(url):
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "dc-montco-hate-crime-pipeline/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            return json.loads(body)
        except Exception as e:
            last_err = e
            print(f"  fetch attempt {attempt+1} failed: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def fetch_socrata_all(endpoint, order_field=None):
    """Full paginated pull - used only for the small Bias Incidents dataset."""
    all_rows = []
    offset = 0
    while True:
        params = {"$limit": str(PAGE_SIZE), "$offset": str(offset)}
        if order_field:
            params["$order"] = order_field
        url = endpoint + "?" + urllib.parse.urlencode(params)
        rows = fetch_url(url)
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows


def fetch_crime_by_case_numbers(case_numbers):
    """Targeted fetch: only pulls Crime rows matching known case numbers,
    via batched SoQL `$where=case_number in (...)` queries. Avoids ever
    downloading the full multi-hundred-thousand-row Crime table."""
    results = []
    case_numbers = [c for c in case_numbers if c]
    for i in range(0, len(case_numbers), CASE_NUMBER_BATCH_SIZE):
        batch = case_numbers[i:i + CASE_NUMBER_BATCH_SIZE]
        quoted = ",".join(f"'{c}'" for c in batch)
        where_clause = f"case_number in ({quoted})"
        params = {"$where": where_clause, "$limit": str(len(batch) * 5)}
        url = CRIME_ENDPOINT + "?" + urllib.parse.urlencode(params)
        rows = fetch_url(url)
        results.extend(rows)
        print(f"  fetched batch {i // CASE_NUMBER_BATCH_SIZE + 1} "
              f"({len(batch)} case numbers -> {len(rows)} crime rows)")
    return results


def main():
    print("Fetching MCPD Bias Incidents (7bhj-887p)...")
    bias_rows = fetch_socrata_all(BIAS_ENDPOINT, order_field="incident_date")
    print(f"  {len(bias_rows)} bias incident records.")

    case_numbers = [b.get("id") for b in bias_rows if b.get("id")]
    print(f"Fetching matching Crime records for {len(case_numbers)} case numbers "
          f"(targeted query, not a full table pull)...")
    crime_rows = fetch_crime_by_case_numbers(case_numbers)
    print(f"  {len(crime_rows)} matching crime records fetched.")

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
