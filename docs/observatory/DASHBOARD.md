# Evidence panel on the observatory dashboard (MIA-OBS-06)

Open the observatory dashboard (`/`), prepare or select an experiment, then **View evidence report**. The panel is read-only and built only from stored records.

- **Coverage:** captured vs planned days, missing days, days Mia was explicitly offline, and days nobody accounts for.
- **Metric changes:** per metric, a small trend drawn from the recorded samples only (dashed wherever days have no evidence), each before/after step with its delta, status, candidate changes (commit, capability transitions, dependency versions, models seen serving requests), unknown factors, and "causal claim: not allowed".
- **Benchmarks and timing:** suite runs with version and first-vs-last result only when comparable; timing runs with repeat counts.
- **Drill-down:** every metric step and benchmark/timing run has buttons that show the raw stored record (`GET /api/observatory/metrics/{sample_id}`, `GET /api/observatory/events/{event_id}`), including its measurement/definition version metadata.
- **Honest states:** no experiment, no samples or no comparable runs show an explanatory message; nothing is seeded or interpolated. Stored text (experiment names, run labels) is inserted as text, never HTML.
- **Versions:** report schema, report hash, baseline hash and each snapshot's manifest hash.

Routes (all GET, no writes): `/api/experiments/{id}/evidence`, `/api/observatory/events/{event_id}`, `/api/observatory/metrics/{sample_id}`.

Not covered: start/pause/resume/stop/export controls and the privacy notice (OBS-08, which acts on your real experiment), and any judgement of how the page looks. The tests use real Chromium but no human review.

Pre-existing bug fixed on the way: the page used an undefined `$` helper, so a top-level call threw and the page's startup calls never ran (the experiment panel stayed on "Not prepared" until a manual refresh action). `$` is now an alias of `el`, with a regression test.
