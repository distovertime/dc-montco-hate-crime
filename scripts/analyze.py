"""
analyze.py

Reads data/events.json (output of build_data.py) and computes all the
derived analytics the dashboard needs, writing data/analytics.json:

  - c3, c5:        per-group DBSCAN spatio-temporal clusters
                   (min_samples=3 and min_samples=5 respectively)
  - km_all, km_25: KMeans hotspots over all-time events / 2025+ events only
  - preds:         exponential inter-arrival "next event" prediction per
                   group, fit on 2023+ events, with a confidence interval
                   and the group's current hotspot location
  - risk:          0-100 risk score per group:
                       vr    = events / population * 100000
                       raw   = vr * avg_severity * xf
                       score = raw / max(raw) * 100
                   population and xf are external reference figures (not
                   derivable from event data) - see POPULATION_REF / XF_REF
                   below. If a group in the data isn't in these reference
                   tables, it's skipped from the risk table rather than
                   guessed at.
  - monthly:       event counts per month
  - jewish_bd, asian_bd: subgroup breakdowns from DC's raw TARGETED_GROUP
                   field (subgroup_raw), only available for DC records

NOTE ON EXACTNESS: the historical dashboard's RISK/PREDS numbers were
computed on a data scope (date window, dedup rules) that could not be
fully recovered from what's embedded in the existing HTML - ratios
between groups don't match any single consistent filter. The formulas
here are faithful to the confirmed methodology, but re-running against
fresh weekly data will not reproduce the old frozen numbers exactly, by
design: every run recomputes cleanly from whatever is in events.json.
"""

import json
import os
import math
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import DBSCAN, KMeans

EVENTS_PATH = os.path.join("data", "events.json")
OUT_PATH = os.path.join("data", "analytics.json")

# --- Reference tables (external data, not derivable from events) ---------
# Population denominators used in the risk score. Approximate figures for
# the DC + Montgomery County combined population by group. These should be
# reviewed/updated periodically; they are not computed from event data.
POPULATION_REF = {
    "Transgender": 40000,
    "Jewish": 300000,
    "Homosexual": 250000,
    "Islamic": 150000,
    "Black": 1800000,
    "Hispanic": 600000,
    "Asian": 350000,
    "White": 2000000,
}

# Multiplier reflecting under-reporting / vulnerability adjustment per group.
XF_REF = {
    "Transgender": 1.5,
    "Jewish": 1.8,
    "Homosexual": 1.2,
    "Islamic": 1.6,
    "Black": 1.2,
    "Hispanic": 1.3,
    "Asian": 1.2,
    "White": 1.0,
}

DBSCAN_EPS_KM = 1.0      # spatial radius in km treated as "same area"
DBSCAN_EPS_DAYS = 30     # temporal window in days treated as "same spell"
PRED_MIN_YEAR = 2023     # inter-arrival model fit window ("2023+ data")
GY_MIN_YEAR = 2023       # yearly group breakdown window (confirmed via ground truth)
CAL_MIN_DATE = "2022-01-01"  # calendar heatmap window (confirmed via ground truth)
KM25_MIN_YEAR = 2025     # KM_25 hotspot subset ("2025+ events")
KMEANS_K = 5


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_events():
    with open(EVENTS_PATH) as f:
        events = json.load(f)
    for e in events:
        e["_date"] = datetime.strptime(e["date"], "%Y-%m-%d")
    return events


def spatio_temporal_dbscan(events, min_samples):
    """Cluster events within a group using a combined space+time metric:
    scale days into the same units as km by treating DBSCAN_EPS_DAYS as
    'equivalent' to DBSCAN_EPS_KM, then run DBSCAN with eps=1 on the
    jointly-scaled 3D feature (lat/lon converted to km offsets, plus
    scaled day offset)."""
    clusters_out = []
    by_group = defaultdict(list)
    for e in events:
        by_group[e["group"]].append(e)

    for group, evs in by_group.items():
        if len(evs) < min_samples:
            continue
        evs_sorted = sorted(evs, key=lambda e: e["_date"])
        t0 = evs_sorted[0]["_date"]
        lat0 = evs_sorted[0]["lat"]
        lon0 = evs_sorted[0]["lon"]

        feats = []
        for e in evs_sorted:
            dx = (e["lon"] - lon0) * 111.0 * math.cos(math.radians(lat0))
            dy = (e["lat"] - lat0) * 111.0
            dt_days = (e["_date"] - t0).days
            dt_scaled = dt_days * (DBSCAN_EPS_KM / DBSCAN_EPS_DAYS)
            feats.append([dx, dy, dt_scaled])

        X = np.array(feats)
        labels = DBSCAN(eps=DBSCAN_EPS_KM, min_samples=min_samples).fit_predict(X)

        cluster_ids = defaultdict(list)
        for idx, lbl in enumerate(labels):
            if lbl == -1:
                continue
            cluster_ids[lbl].append(evs_sorted[idx])

        for i, (lbl, members) in enumerate(cluster_ids.items()):
            key = f"{group}_{i}"
            dates = [m["_date"] for m in members]
            lats = [m["lat"] for m in members]
            lons = [m["lon"] for m in members]
            sevs = [m["severity"] for m in members if m["severity"] is not None]
            offense_counts = Counter(m["offense"] for m in members if m["offense"])
            top_offense = offense_counts.most_common(1)[0][0] if offense_counts else None
            state_counts = Counter(m["state"] for m in members)
            top_state = state_counts.most_common(1)[0][0]

            clusters_out.append({
                "key": key,
                "group": group,
                "events": len(members),
                "start": min(dates).strftime("%Y-%m-%d"),
                "end": max(dates).strftime("%Y-%m-%d"),
                "days": (max(dates) - min(dates)).days,
                "lat": round(sum(lats) / len(lats), 5),
                "lon": round(sum(lons) / len(lons), 5),
                "state": top_state,
                "offense": top_offense,
                "sev": round(sum(sevs) / len(sevs), 1) if sevs else None,
                "member_ids": [m.get("id") for m in members if m.get("id")],
            })
    return clusters_out


def kmeans_hotspots(events, k=KMEANS_K):
    if len(events) < k:
        return []
    coords = np.array([[e["lat"], e["lon"]] for e in events])
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(coords)
    hotspots = []
    for i in range(k):
        members = [e for e, lbl in zip(events, km.labels_) if lbl == i]
        if not members:
            continue
        center = km.cluster_centers_[i]
        hotspots.append({
            "lat": round(float(center[0]), 5),
            "lon": round(float(center[1]), 5),
            "events": len(members),
        })
    hotspots.sort(key=lambda h: -h["events"])
    return hotspots


def inter_arrival_predictions(events):
    """Exponential inter-arrival model fit on PRED_MIN_YEAR+ events per
    group: mean_gap = mean(days between consecutive events), predicted
    next date = last_date + mean_gap, with a rough 90% CI derived from the
    exponential distribution's spread."""
    by_group = defaultdict(list)
    for e in events:
        if e["_date"].year >= PRED_MIN_YEAR:
            by_group[e["group"]].append(e)

    preds = []
    for group, evs in by_group.items():
        evs_sorted = sorted(evs, key=lambda e: e["_date"])
        if len(evs_sorted) < 3:
            continue
        gaps = [
            (evs_sorted[i]["_date"] - evs_sorted[i - 1]["_date"]).days
            for i in range(1, len(evs_sorted))
        ]
        mean_gap = sum(gaps) / len(gaps) if gaps else None
        if not mean_gap:
            continue
        last_date = evs_sorted[-1]["_date"]
        next_date = last_date + timedelta(days=mean_gap)
        # Exponential distribution: 90% CI roughly [gap*0.05, gap*2.3] scaled by mean.
        ci_lo = last_date + timedelta(days=mean_gap * 0.05)
        ci_hi = last_date + timedelta(days=mean_gap * 2.3)

        # Hotspot = centroid of this group's most recent cluster of events
        # (simple recency-weighted centroid over the whole 2023+ window).
        lat_c = sum(e["lat"] for e in evs_sorted) / len(evs_sorted)
        lon_c = sum(e["lon"] for e in evs_sorted) / len(evs_sorted)
        state_counts = Counter(e["state"] for e in evs_sorted)
        top_state = state_counts.most_common(1)[0][0]

        preds.append({
            "group": group,
            "last": last_date.strftime("%Y-%m-%d"),
            "next": next_date.strftime("%Y-%m-%d"),
            "ci_lo": ci_lo.strftime("%Y-%m-%d"),
            "ci_hi": ci_hi.strftime("%Y-%m-%d"),
            "gap": round(mean_gap, 1),
            "n": len(evs_sorted),
            "hot_lat": round(lat_c, 5),
            "hot_lon": round(lon_c, 5),
            "hot_state": top_state,
        })

    preds.sort(key=lambda p: p["n"], reverse=True)
    return preds


def risk_scores(events):
    by_group = defaultdict(list)
    for e in events:
        by_group[e["group"]].append(e)

    rows = []
    for group, pop in POPULATION_REF.items():
        evs = by_group.get(group, [])
        n = len(evs)
        sevs = [e["severity"] for e in evs if e["severity"] is not None]
        avg_sev = sum(sevs) / len(sevs) if sevs else 0
        xf = XF_REF.get(group, 1.0)
        vr = n / pop * 100000
        raw = vr * avg_sev * xf
        rows.append({
            "group": group, "events": n, "sev": round(avg_sev, 1),
            "pop": pop, "vr": round(vr, 2), "xf": xf, "raw": round(raw, 2),
        })

    max_raw = max((r["raw"] for r in rows), default=1) or 1
    for r in rows:
        r["score"] = round(r["raw"] / max_raw * 100, 1)
    rows.sort(key=lambda r: -r["score"])
    return rows


def monthly_counts(events):
    counts = Counter(
        e["_date"].strftime("%Y-%m") for e in events if e["date"] >= CAL_MIN_DATE
    )
    return [{"date": m, "count": c} for m, c in sorted(counts.items())]


def yearly_group_counts(events):
    """GY_DATA: {group: {year: count}} for events from GY_MIN_YEAR onward."""
    gy = defaultdict(lambda: defaultdict(int))
    for e in events:
        if e["_date"].year >= GY_MIN_YEAR:
            gy[e["group"]][str(e["_date"].year)] += 1
    return {g: dict(years) for g, years in gy.items()}


def stacked_from_gy(gy_data):
    """STACKED: {group: [count per year]}, 0-filled, years sorted ascending
    across the full range present in gy_data."""
    all_years = sorted({int(y) for years in gy_data.values() for y in years})
    if not all_years:
        return {}
    year_range = list(range(all_years[0], all_years[-1] + 1))
    stacked = {}
    for group, years in gy_data.items():
        stacked[group] = [years.get(str(y), 0) for y in year_range]
    return stacked, year_range


def offense_breakdown(events):
    """OFFENSE_GRP: {group: [{offense, count}, ...top 5]}"""
    by_group = defaultdict(Counter)
    for e in events:
        if e.get("offense"):
            by_group[e["group"]][e["offense"]] += 1
    result = {}
    for group, counter in by_group.items():
        result[group] = [
            {"offense": off, "count": cnt}
            for off, cnt in counter.most_common(5)
        ]
    return result


def calendar_daily_counts(events):
    """CAL_DATA: {date: count} for events from CAL_MIN_DATE onward."""
    counts = Counter(
        e["date"] for e in events if e["date"] >= CAL_MIN_DATE
    )
    return dict(counts)


def subgroup_breakdowns(events):
    """Compute Jewish (Israeli/ethnic vs religious) and Asian (per-ethnicity)
    breakdowns from DC's raw TARGETED_GROUP field. Only DC records carry
    subgroup_raw; MCPD contributes to the "Religious"/"Asian" (generic)
    bucket implicitly by not having subgroup detail."""
    jewish_ethnic_tags = {"Israeli"}
    asian_ethnic_tags = {
        "Chinese", "Korean", "Indian", "Pakistani", "Japanese",
        "Taiwanese", "Vietnamese", "Filipino",
    }

    jewish_counts = Counter()
    asian_counts = Counter()

    for e in events:
        if e["group"] != "Jewish" and e["group"] != "Asian":
            continue
        raw = e.get("subgroup_raw") or ""
        raw_parts = [p.strip() for p in raw.replace("Anti-", "").split(";")]

        if e["group"] == "Jewish":
            if any(p in jewish_ethnic_tags for p in raw_parts):
                jewish_counts["Israeli/Ethnic"] += 1
            else:
                jewish_counts["Religious"] += 1

        if e["group"] == "Asian":
            matched = [p for p in raw_parts if p in asian_ethnic_tags]
            if matched:
                for p in matched:
                    asian_counts[p] += 1
            else:
                asian_counts["Asian"] += 1

    jewish_bd = [{"subtype": k, "count": v} for k, v in jewish_counts.items()]
    asian_bd = [{"ethnicity": k, "count": v} for k, v in asian_counts.most_common()]
    return jewish_bd, asian_bd


def main():
    events = load_events()
    print(f"Loaded {len(events)} events.")

    c3 = spatio_temporal_dbscan(events, min_samples=3)
    c5 = spatio_temporal_dbscan(events, min_samples=5)
    print(f"C3 clusters: {len(c3)}, C5 clusters: {len(c5)}")

    km_all = kmeans_hotspots(events, k=KMEANS_K)
    events_25 = [e for e in events if e["_date"].year >= KM25_MIN_YEAR]
    km_25 = kmeans_hotspots(events_25, k=min(KMEANS_K, max(1, len(events_25))))
    print(f"KM_ALL hotspots: {len(km_all)}, KM_25 hotspots: {len(km_25)} (from {len(events_25)} events)")

    preds = inter_arrival_predictions(events)
    print(f"Predictions for {len(preds)} groups.")

    risk = risk_scores(events)
    monthly = monthly_counts(events)
    jewish_bd, asian_bd = subgroup_breakdowns(events)

    gy_data = yearly_group_counts(events)
    stacked, stacked_years = stacked_from_gy(gy_data)
    offense_grp = offense_breakdown(events)
    cal_data = calendar_daily_counts(events)
    print(f"GY_DATA groups: {len(gy_data)}, STACKED years: {stacked_years}")
    print(f"OFFENSE_GRP groups: {len(offense_grp)}, CAL_DATA days: {len(cal_data)}")

    analytics = {
        "c3": c3, "c5": c5,
        "km_all": km_all, "km_25": km_25,
        "preds": preds, "risk": risk,
        "monthly": monthly,
        "jewish_bd": jewish_bd, "asian_bd": asian_bd,
        "gy_data": gy_data, "stacked": stacked, "stacked_years": stacked_years,
        "offense_grp": offense_grp, "cal_data": cal_data,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(analytics, f, indent=None)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
