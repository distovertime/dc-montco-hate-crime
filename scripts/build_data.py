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

BIAS_TYPE_TO_GROUP = {
    "Sexual Orientation": "Homosexual",
    "Gender Identity": "Transgender",
    "Disability": "Disabled",
    "Homelessness": "Homeless",
}


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
    elif hate_bias_type and hate_bias_type.strip() in BIAS_TYPE_TO_GROUP:
        group_labels = [BIAS_TYPE_TO_GROUP[hate_bias_type.strip()]]
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
            "state": "DC",
            "address": address,
            "offense": offense,
            "severity": score_offense(offense),
        })
    return events


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

    address_parts = [
        rec.get("address_number"), rec.get("address_street"), rec.get("street_type")
    ]
    address = " ".join(p for p in address_parts if p) or None

    bias_1 = rec.get("bias_code") or rec.get("bias")
    bias_2 = rec.get("bias_code_2")
    # NOTE: the Socrata field literally named "bias" is confusingly the
    # OFFENSE-type description ("Vandalism", "Verbal Intimidation/Simple
    # Assault", etc.) per the dataset's own column description ("Describes
    # how the bias was manifested by the offender") - confirmed against the
    # live schema. "bias_code"/"bias_code_2" are the actual bias/group
    # fields, handled separately above.
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
        events.append({
            "date": date_str,
            "lat": lat,
            "lon": lon,
            "group": group_label or "Unspecified",
            "subgroup_raw": None,  # subgroup detail only captured on DC side
            "state": "MD",
            "address": address,
            "offense": offense,
            "severity": score_offense(offense),
        })
    return events


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
        return LAT_MIN <= e["lat"] <= LAT_MAX and LON_MIN <= e["lon"] <= LON_MAX

    clean_events = [
        e for e in events
        if e.get("date") and e.get("lat") is not None and e.get("lon") is not None
        and in_region(e)
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
