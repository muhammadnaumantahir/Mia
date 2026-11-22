# Change attribution (MIA-OBS-04, first slice)

Code: `src/mia/observatory/attribution.py` — `build_attribution(store, experiment_id, metric_names=None)`.

For every pair of consecutive captured days it lists the changes the stores recorded between them (commit change; capability status transitions with counts), then joins each metric delta to the changes in the same window.

- **Candidates, never causes.** Every row has `causal_claim_allowed: false` with the reason: sequential observations without a controlled comparison cannot show that a change caused a metric to move. Row status is one of `no_recorded_change_in_window`, `single_candidate_correlation_only`, `confounded_multiple_candidates`, `window_not_covered_by_evidence`.
- **Gaps stay visible.** Days with no evidence inside a window are listed per row; a metric span the snapshots do not cover is not attributed to anything; an unavailable capability source contributes no claim either way.
- **Not attributable today, stated in every report:** model/provider (the usage summary has no per-model breakdown) and dependency versions (snapshots do not store them). Closing these needs a per-model usage breakdown in the ledger and storing `dependencies` in each daily snapshot; both touch other owners' code and were not changed here.
- **Unobserved factors** (host load and time of day, what conversations happened, model behaviour outside Mia's control) are listed in the report.
- Configuration changes are not listed because daily capture already refuses a snapshot whose configuration hash differs from Day 0.

Not built yet: any controlled comparison (A/B or repeated-run design) that could justify a causal claim; OBS-06 and OBS-07 can display this report but must not upgrade its wording.
