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

## Lifecycle controls (MIA-OBS-08, dashboard half)

In the "30-day experiment" panel, built on the backend routes from the OBS-08 backend slice:

- **What a 30-day run records** shows the privacy notice (version, what is stored and where, what is recorded, never collected, controls, live resource caps), inserted as text.
- **Start** stays disabled until the notice's checkbox is ticked; the click sends `{"privacy_notice_version": N}` and the server records it with the start event. (The acknowledgement is enforced in the page, not by the server: a client calling the API directly can still start without it, and the event then records `None`.)
- **Pause/Resume auto capture** follow the scheduler's real state; both stay disabled when automatic capture is off.
- **Stop experiment…** needs a second explicit confirmation ("Yes, stop this experiment"), explains that it is final, keeps all recorded evidence and never marks the run completed.
- **Export evidence** verifies stored hashes first; a refusal is shown with its reason and nothing downloads. A successful export shows the offline check command `python -m mia.observatory.experiment_export verify FILE`.

Nothing here starts, stops or exports on its own. The owner presses the buttons on the real experiment; the tests use temporary databases only.
