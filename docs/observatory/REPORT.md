# Experiment report generator (MIA-OBS-07)

Code: `src/mia/observatory/report_generator.py` — `generate_report(store, experiment_id)`, `render_markdown(report)`, `verify_report(store, experiment_id, report)`.

It extends the existing integrity-checked `build_experiment_report` (hash-verified baseline and snapshots, missing/unavailable days, events) and adds:

- **Sample sizes** per metric and per benchmark suite, with a small-sample flag under 30 items/samples.
- **Metric changes** from OBS-04 attribution (correlation only), **benchmark runs** from OBS-05 with first-vs-last comparison only under equivalent conditions.
- **Rollback history** from per-window capability transition counts; an unavailable source never produces a claim.
- **Confounders generated from the evidence** (commit changes, capability transitions, missing days, resource-pressure deferrals, failed captures) plus fixed ones (model/provider and dependency versions not recorded, task mix not controlled).
- **Limitations and claims not allowed**: no claim that Mia improved, that a change caused a result, or that results hold for live models.
- **Reproducible**: no generation time is mixed in; the same stored evidence gives the same report and `report_sha256`. `verify_report` regenerates and compares, and fails once new evidence exists. Generating a report writes nothing.

Not built: dashboard display (OBS-06), export/lifecycle controls (OBS-08), and any report on days that have not happened. A real Day 0–30 report needs the owner to run the experiment.
