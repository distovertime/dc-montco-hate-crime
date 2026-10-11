# Handoff notes (for starting a fresh chat)

## Project
- Live: https://distovertime.github.io/dc-montco-hate-crime/ (repo: distovertime/dc-montco-hate-crime)
- Pipeline: .github/workflows/update-data.yml -> scripts/fetch_dc.py, fetch_mcpd.py, build_data.py, analyze.py -> data/events.json + data/analytics.json
- Schedule: Saturday 04:00 UTC (midnight Eastern during EDT). Shift to '0 5 * * 6' during EST (Nov-Mar).
- Credentials: Ron supplies a fine-grained GitHub token per session (Contents, Workflows, Actions = read/write). Never commit it.
- CARTO basemap key is in the tile URL in index.html (required since late Aug 2026; fine to be public).

## Done: "Suspicious Patterns" banner
- Wording: "Suspicious" (not "Active"); "Escalating" tag kept; calm text "No active suspicious patterns detected" + "No cluster has had a new event in the last 30 days.". Own section after the header, before Overview. One card per flagged cluster; calm = teal, alert = coral.
- Backend: analytics.json `active_patterns` (currently [] = quiet week).
- Frontend: `ACTIVE_PATTERNS` loaded in loadDashboardData(); `renderSuspiciousPatterns()` called in initDashboard next to updateOverviewStats(); cards click through to flyToCluster (bound via addEventListener, no window.* needed).
- Not yet seen with real data (none flagged). Verified only with sample data via a node test of the render function.

## Fixed: banner/table click-through (2026-10-10)
- flyToCluster() now clears calendar selection + date slider (a stale selection hid clusters whose span missed it), moves the calendar to the cluster's start month, and lists the cluster's own events under the calendar (via member_ids) instead of selecting dates, since date selection also lists non-member events from the same days.
- flyToCluster(key,group,lat,lon,thr): optional thr. Banner passes 3. Keys like Jewish_2 exist in BOTH C3 and C5 as different clusters, so key-only lookup can open the wrong one (table rows still use the old lookup; known gap).
- Known gap: cluster table shows only the top 20 by size (slice(0,20)) under an "All clusters" tab label, so small recent clusters (e.g. Jewish_33, rank 95 of 98) never appear in it. Tab label says ">3 events" but clusters are 3+.

## Shipped (2026-10-10): cluster table, calendar zoom, tile seams
- Cluster table: active clusters (last event within 30 days of the newest event in the data, same rule as the banner) are pinned first with a coral "Active" pill + row tint, then the 20 largest. Caption under the tabs explains the order. Row clicks now pass the right threshold (3 or 5) to flyToCluster, which fixes the Jewish_2-style key collision for table rows too.
- Labels: ">3"/">5 events" changed to "3+"/"5+" (tabs and metric tiles) since 3- and 5-event clusters are included.
- Calendar: selecting a day/range (month grid or heat strip) now zooms the map to those events via zoomToSelectedDates() (fitBounds, maxZoom 14).
- Tile seams: added `.leaflet-tile-container img{outline:1px solid transparent}`. UNVERIFIED best guess for thin horizontal/vertical lines at max zoom; Ron reports lines appear when zoomed all the way in. If they persist, next ideas: cap maxZoom at 18/19 with maxNativeZoom, or check for overzoom/devicePixelRatio issues.

## Gotchas learned
- Always `git pull` before pushing: the workflow commits data files too.
- Set git user.name/email in a fresh clone or commits silently fail.
- Functions used by inline onclick must be exposed via window.* inside initDashboard.
- raw.githubusercontent.com "main" can lag; verify by commit SHA.
- Nominatim/Census/Socrata domains are not reachable from the sandbox, only from the Action runner.
- Not saved anywhere: the sandbox-only extract_tier1.py for the us-hate-crime-dashboard repo (needs re-creating from Ron's 2018 xlsx).
