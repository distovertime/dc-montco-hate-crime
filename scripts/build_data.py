"""
build_data.py

Merges the DC (fetch_dc.py) and Montgomery County (fetch_mcpd.py) raw pulls
into a single cleaned data/events.json, applying the cleaning rules
confirmed against the original dashboard / transcript:

  1. Strip "Anti-" prefixes from bias/group labels ("Anti-Jewish" -> "Jewish").
  2. Explode dual-bias records into two separate events (one per bias),
     sharing the same date/location/offense.
  3. Assign the targeted group from the Type-of-Hate-Bias field when the
     group field itself is blank/missing.
  4. Offense-leak correction: if a "group" value is actually an offense
     code (e.g. "ADW") that leaked into the wrong column, move it to the
     offense field and fall back the group to "Unspecified".
  5. Score each event's severity from its offense type via a fixed
     offense -> severity lookup table (scripts/offense_severity_map.json),
     reverse-engineered from the existing dashboard's data and confirmed
     to be a clean 1:1 mapping (each offense always gets the same score).
  6. Preserve DC's raw TARGETED_GROUP granularity (Israeli, Chinese,
     Korean, etc.) alongside the collapsed group, for the Jewish/Asian
     subgroup breakdown charts computed downstream in analyze.py.

Output schema (data/events.json): a list of event dicts:
  {
    "date": "YYYY-MM-DD",
    "lat": float,
    "lon": float,
    "group": str,            # collapsed/canonical group name
    "subgroup_raw": str|None,# DC's raw TARGETED_GROUP value, if available
    "state": "DC"|"MD",
    "address": str,
    "offense": str,
    "severity": int
  }
"""

import json
import os
import re
import hashlib
import time
import urllib.request
import urllib.parse

RAW_DC = os.path.join("data", "raw", "dc_raw.json")
RAW_MCPD = os.path.join("data", "raw", "mcpd_raw.json")
OUT_PATH = os.path.join("data", "events.json")
SEV_MAP_PATH = os.path.join(os.path.dirname(__file__), "offense_severity_map.json")

with open(SEV_MAP_PATH) as f:
    SEVERITY_MAP = json.load(f)

OFFENSE_TOKENS = set(SEVERITY_MAP.keys())

SUBGROUP_TO_GROUP = {
    # Jewish
    "Israeli": "Jewish", "Jewish": "Jewish",
    # Asian (specific ethnicities)
    "Chinese": "Asian", "Korean": "Asian", "Indian": "Asian",
    "Pakistani": "Asian", "Japanese": "Asian", "Taiwanese": "Asian",
    "Vietnamese": "Asian", "Filipino": "Asian",
    # Black / African
    "African": "Black", "Black": "Black", "Black/African": "Black",
    "Ethiopian": "Black", "Oromo": "Black", "Jamaican": "Black",
    # Hispanic / Latino (nationality-level labels)
    "Latino/Hispanic": "Hispanic", "Mexican": "Hispanic",
    "Guatemalan": "Hispanic", "Colombian": "Hispanic",
    "Puerto Rican": "Hispanic", "Venezuelan": "Hispanic",
    # Sexual orientation variants -> canonical "Homosexual" bucket
    "Male Homosexual": "Homosexual", "Female Homosexual": "Homosexual",
    "Bisexual": "Homosexual", "Homosexual": "Homosexual",
    # Gender identity
    "Gender Non-Conforming": "Transgender",
    # Islamic / Muslim
    "Muslim": "Islamic", "Islamic": "Islamic",
    # Arab / Middle Eastern
    "Middle Eastern": "Arab", "Arab/Middle Eastern": "Arab",
    "Palestinian": "Arab", "Non-Palestinian": "Arab", "Lebanese": "Arab",
    # Disability
    "Mental Disability": "Disabled", "Physical Disability": "Disabled",
    # Spelling / naming normalization
    "Hindhu": "Hindu", "Homelessness": "Homeless", "Unknown": "Unspecified",
    "Multiple": "Unspecified",
    # Ethnicities without a clean canonical bucket -> Other Ethnicity
    "Iranian": "Other Ethnicity", "Turkish": "Other Ethnicity",
    "Russian": "Other Ethnicity", "Ukrainian": "Other Ethnicity",
    "Non-European": "Other Ethnicity",
}

BIAS_TYPE_KEYWORDS = [
    ("sexual orientation", "Homosexual"),
    ("gender", "Transgender"),  # covers "Gender Identity", "Gender Identity or Expression", etc.
    ("disability", "Disabled"),
    ("homeless", "Homeless"),
    ("political", "Political Group"),
]


def classify_bias_type_part(part):
    """Keyword/substring match instead of requiring an exact string, since
    DC's official wording for a category (e.g. 'gender identity or
    expression') doesn't always match a short label exactly."""
    if not part:
        return None
    part_lower = part.lower()
    for kw, group in BIAS_TYPE_KEYWORDS:
        if kw in part_lower:
            return group
    return None


def collapse_group(label):
    if not label:
        return label
    return SUBGROUP_TO_GROUP.get(label.strip(), label.strip())


def strip_anti(label):
    if not label:
        return label
    return re.sub(r"^Anti-", "", label.strip())


def split_dual_bias(label):
    """Split a combined bias label like 'Jewish; Hispanic' into parts.
    Returns a list of 1 or 2+ cleaned labels."""
    if not label:
        return [label]
    parts = re.split(r"\s*;\s*|\s+and\s+", label.strip())
    return [strip_anti(p) for p in parts if p]


def score_offense(offense_label):
    if not offense_label:
        return None
    return SEVERITY_MAP.get(offense_label.strip())


def clean_dc_record(rec):
    """Normalize one raw DC ArcGIS feature attribute dict into 1+ events."""
    date_raw = rec.get("DATE_OF_OFFENSE")
    if date_raw is None:
        return []
    # ArcGIS returns epoch millis for date fields.
    try:
        import datetime
        date_str = datetime.datetime.fromtimestamp(int(date_raw) / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        date_str = str(date_raw)[:10]

    lat = rec.get("MAR_LATITUDE")
    lon = rec.get("MAR_LONGITUDE")
    address = rec.get("ADDRESS")
    offense = rec.get("TOP_OFFENSE_TYPE")
    targeted_group_raw = rec.get("TARGETED_GROUP")
    hate_bias_type = rec.get("TYPE_OF_HATE_BIAS")

    group_source = targeted_group_raw or ""

    if group_source.strip() in OFFENSE_TOKENS:
        if not offense:
            offense = group_source.strip()
        group_source = ""

    if group_source:
        group_labels = split_dual_bias(group_source)
    elif hate_bias_type:
        parts = split_dual_bias(hate_bias_type)
        classified = [classify_bias_type_part(p) for p in parts]
        classified = [c for c in classified if c]
        group_labels = classified if classified else ["Unspecified"]
    else:
        group_labels = ["Unspecified"]

    events = []
    for group_label in group_labels:
        canonical_group = collapse_group(group_label) or "Unspecified"
        events.append({
            "date": date_str,
            "lat": lat,
            "lon": lon,
            "group": canonical_group,
            "subgroup_raw": targeted_group_raw,
            "bias_type_raw": hate_bias_type,  # kept for diagnostics if the
                                               # fallback classifier ever
                                               # needs re-tuning
            "state": "DC",
            "address": address,
            "city": "Washington",  # DC has no separate city field; matches
                                   # the Tableau dashboard's own convention
                                   # of treating all of DC as one city row.
            "place": None,  # not available in the DC source data
            "offense": offense,
            "severity": score_offense(offense),
        })
    return events


CITY_NORMALIZE = {
    # Known truncated/misspelled values in Montgomery County's own city
    # field - confirmed by inspecting the live breakdown table, not
    # something introduced by this pipeline.
    "Oney": "Olney",
    "Ro": "Rockville",
    "Ga": "Gaithersburg",
}


def title_case_address(s):
    """Like str.title(), but correct for street names: '16TH' -> '16th'
    (not '16Th'). Only capitalizes a word's first letter if that word
    actually starts with a letter; words starting with a digit (ordinals
    like 16TH, 1ST, 3RD) are just lowercased instead."""
    if not s:
        return s
    words = s.split(" ")
    out = []
    for w in words:
        if w and w[0].isalpha():
            out.append(w[0].upper() + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)


def clean_mcpd_record(rec):
    """Normalize one joined MCPD bias-incident dict into 1+ events."""
    date_raw = rec.get("incident_date")
    date_str = str(date_raw)[:10] if date_raw else None
    if not date_str:
        return []

    lat = rec.get("latitude")
    lon = rec.get("longitude")
    try:
        lat = float(lat) if lat is not None else None
        lon = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lat, lon = None, None

    address_number = rec.get("address_number")
    street_parts = [rec.get("address_street"), rec.get("street_type")]
    street_name = " ".join(p for p in street_parts if p)
    street_name = title_case_address(street_name)
    if address_number and street_name:
        address = f"{address_number} block of {street_name}"
    else:
        address = street_name or (str(address_number) if address_number else None)
    city = rec.get("city")
    city = city.title() if city else None
    city = CITY_NORMALIZE.get(city, city)
    place = rec.get("place")

    bias_1 = rec.get("bias_code")
    bias_2 = rec.get("bias_code_2")
    # NOTE: the Socrata field literally named "bias" is confusingly the
    # OFFENSE-type description ("Vandalism", "Verbal Intimidation/Simple
    # Assault", etc.) per the dataset's own column description ("Describes
    # how the bias was manifested by the offender") - confirmed against the
    # live schema. "bias_code"/"bias_code_2" are the actual bias/group
    # fields. Do NOT fall back to "bias" here - it's offense text, not a
    # group label, and would corrupt group assignment if bias_code is ever
    # blank.
    offense = rec.get("bias")

    raw_labels = []
    if bias_1:
        raw_labels.extend(split_dual_bias(bias_1))
    if bias_2:
        raw_labels.extend(split_dual_bias(bias_2))
    if not raw_labels:
        raw_labels = ["Unspecified"]

    events = []
    for group_label in raw_labels:
        canonical_group = collapse_group(group_label) or "Unspecified"
        events.append({
            "date": date_str,
            "lat": lat,
            "lon": lon,
            "group": canonical_group,
            "subgroup_raw": group_label if group_label != canonical_group else None,
            "state": "MD",
            "address": address,
            "city": city,
            "place": place,
            "offense": offense,
            "severity": score_offense(offense),
        })
    return events


def geocode_address_census(address, city, state):
    """Free US address geocoding via the Census Bureau's public geocoder.
    Used only as a fallback for events whose source data had a known
    address/city but a bad/placeholder lat-lon (e.g. Montgomery County
    records where geocoding failed upstream and (0,0) was stored instead
    of leaving the field blank). Returns (lat, lon) or (None, None)."""
    if not address or not city:
        return None, None
    full_address = f"{address}, {city}, {state}"
    params = {
        "address": full_address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?" + urllib.parse.urlencode(params)
    lat, lon = None, None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dc-montco-hate-crime-pipeline/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        matches = data.get("result", {}).get("addressMatches", [])
        if matches:
            coords = matches[0]["coordinates"]
            lat, lon = coords["y"], coords["x"]
    except Exception as e:
        print(f"  Census geocode failed for '{full_address}': {e}")
    finally:
        time.sleep(0.3)
    return lat, lon


def make_event_id(e):
    """Deterministic ID from event content, stable across pipeline re-runs
    so analyze.py's cluster membership lists can reference events reliably
    even as new records get appended week to week."""
    key = f"{e['date']}|{e['lat']}|{e['lon']}|{e['group']}|{e['offense']}|{e['state']}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def load_json(path):
    if not os.path.exists(path):
        print(f"WARNING: {path} not found, treating as empty.")
        return []
    with open(path) as f:
        return json.load(f)


def main():
    dc_raw = load_json(RAW_DC)
    mcpd_raw = load_json(RAW_MCPD)

    events = []
    for rec in dc_raw:
        events.extend(clean_dc_record(rec))
    for rec in mcpd_raw:
        events.extend(clean_mcpd_record(rec))

    # Drop events with no usable location or date - can't be mapped/analyzed.
    # Also drop events whose coordinates fall well outside the DC/Montgomery
    # County area: some source records use (0,0) as a "geocoding failed"
    # placeholder instead of a true null, and isolated bad records can have
    # sign/parsing errors (e.g. positive instead of negative longitude).
    # This is a real DC/MD bounding box with generous padding, not the
    # literal city limits, so it won't clip legitimate edge-of-county events.
    LAT_MIN, LAT_MAX = 38.0, 40.0
    LON_MIN, LON_MAX = -78.5, -76.0

    def in_region(e):
        return (
            e.get("lat") is not None and e.get("lon") is not None
            and LAT_MIN <= e["lat"] <= LAT_MAX and LON_MIN <= e["lon"] <= LON_MAX
        )

    # Recovery pass: some events have a known address/city but a bad source
    # coordinate (e.g. (0,0) placeholder for a failed upstream geocode).
    # Rather than just dropping these real, dated incidents, try to recover
    # real coordinates via the free Census Bureau geocoder before filtering.
    recovered = 0
    attempted = 0
    for e in events:
        if not in_region(e) and e.get("address") and e.get("city"):
            attempted += 1
            lat, lon = geocode_address_census(e["address"], e["city"], e["state"])
            if lat is not None and lon is not None:
                candidate = {"lat": lat, "lon": lon}
                if in_region(candidate):
                    e["lat"] = lat
                    e["lon"] = lon
                    recovered += 1
    if attempted:
        print(f"Census geocoding fallback: recovered {recovered}/{attempted} events with bad source coordinates.")

    clean_events = [
        e for e in events
        if e.get("date") and in_region(e)
    ]
    dropped = len(events) - len(clean_events)

    clean_events.sort(key=lambda e: e["date"])
    for e in clean_events:
        e["id"] = make_event_id(e)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(clean_events, f, indent=None)

    print(f"DC raw records: {len(dc_raw)}")
    print(f"MCPD raw records: {len(mcpd_raw)}")
    print(f"Total events after bias-split: {len(events)}")
    print(f"Dropped (missing date/location): {dropped}")
    print(f"Final events written: {len(clean_events)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
