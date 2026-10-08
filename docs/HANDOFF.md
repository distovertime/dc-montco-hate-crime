# Handoff notes (for starting a fresh chat)

## Project
- Live: https://distovertime.github.io/dc-montco-hate-crime/ (repo: distovertime/dc-montco-hate-crime)
- Pipeline: .github/workflows/update-data.yml -> scripts/fetch_dc.py, fetch_mcpd.py, build_data.py, analyze.py -> data/events.json + data/analytics.json
- Schedule: Saturday 04:00 UTC (midnight Eastern during EDT). Shift to '0 5 * * 6' during EST (Nov-Mar).
- Credentials: Ron supplies a fine-grained GitHub token per session (Contents, Workflows, Actions = read/write). Never commit it.
- CARTO basemap key is in the tile URL in index.html (required since late Aug 2026; fine to be public).

## Done: "Suspicious Patterns" banner
- Wording: "Suspicious" (not "Active"); "Escalating" tag kept; calm text "No suspicious patterns detected". Own section after the header, before Overview. One card per flagged cluster; calm = teal, alert = coral.
- Backend: analytics.json `active_patterns` (currently [] = quiet week).
- Frontend: `ACTIVE_PATTERNS` loaded in loadDashboardData(); `renderSuspiciousPatterns()` called in initDashboard next to updateOverviewStats(); cards click through to flyToCluster (bound via addEventListener, no window.* needed).
- Not yet seen with real data (none flagged). Verified only with sample data via a node test of the render function.

## Gotchas learned
- Always `git pull` before pushing: the workflow commits data files too.
- Set git user.name/email in a fresh clone or commits silently fail.
- Functions used by inline onclick must be exposed via window.* inside initDashboard.
- raw.githubusercontent.com "main" can lag; verify by commit SHA.
- Nominatim/Census/Socrata domains are not reachable from the sandbox, only from the Action runner.
- Not saved anywhere: the sandbox-only extract_tier1.py for the us-hate-crime-dashboard repo (needs re-creating from Ron's 2018 xlsx).
