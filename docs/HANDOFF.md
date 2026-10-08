# Handoff notes (for starting a fresh chat)

## Project
- Live: https://distovertime.github.io/dc-montco-hate-crime/ (repo: distovertime/dc-montco-hate-crime)
- Pipeline: .github/workflows/update-data.yml -> scripts/fetch_dc.py, fetch_mcpd.py, build_data.py, analyze.py -> data/events.json + data/analytics.json
- Schedule: Saturday 04:00 UTC (midnight Eastern during EDT). Shift to '0 5 * * 6' during EST (Nov-Mar).
- Credentials: Ron supplies a fine-grained GitHub token per session (Contents, Workflows, Actions = read/write). Never commit it.
- CARTO basemap key is in the tile URL in index.html (required since late Aug 2026; fine to be public).

## In progress: "Suspicious Patterns" banner
Decisions made with Ron:
- Wording: "Suspicious" (not "Active"). Keep "Escalating". Calm state text: "No suspicious patterns detected".
- Placement: its own section right after the header, before Overview.
- Stack one card per flagged cluster. Calm = teal, alert = coral. Mockup was approved.

Backend DONE: analytics.json has `active_patterns` (currently [] = quiet week). Each item has: cluster_key, group, events, recent_events, lat, lon, start, end, is_escalating, offense_shifted, avg_severity_first_half, avg_severity_second_half, top_offense_first_half, top_offense_second_half, summary, place.

Frontend scaffolding pushed: `<div id="suspiciousPatternsPanel">` section, `.sp-*` CSS classes, `ACTIVE_PATTERNS` global.

Frontend REMAINING:
1. In loadDashboardData(), after the DATA_QUALITY line: `ACTIVE_PATTERNS = analytics.active_patterns || [];`
2. Write renderSuspiciousPatterns(): if empty, one `.sp-calm` block; else one `.sp-alert` card per pattern with title "Suspicious pattern detected - {dispGroup(group)}, {place}", `p.summary` as body, `.sp-tag` chips (event count, start-end dates, and `.sp-tag-escalating` "Escalating" if is_escalating), onclick -> flyToCluster(p.cluster_key, p.group, p.lat, p.lon).
3. Call it from initDashboard next to updateOverviewStats().
4. Test with sample data (real data has none), run node --check, copy index.html to HateCrime_Dashboard_v3_12_9.html, pull then push.

## Gotchas learned
- Always `git pull` before pushing: the workflow commits data files too.
- Set git user.name/email in a fresh clone or commits silently fail.
- Functions used by inline onclick must be exposed via window.* inside initDashboard.
- raw.githubusercontent.com "main" can lag; verify by commit SHA.
- Nominatim/Census/Socrata domains are not reachable from the sandbox, only from the Action runner.
- Not saved anywhere: the sandbox-only extract_tier1.py for the us-hate-crime-dashboard repo (needs re-creating from Ron's 2018 xlsx).
