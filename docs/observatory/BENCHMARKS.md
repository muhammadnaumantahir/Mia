# Versioned benchmarks (MIA-OBS-05, first slice)

Code: `src/mia/observatory/benchmarks.py`. Evidence kind: `deterministic_scripted_provider`.

- **Suites** are the scripted M1 evaluation scenarios grouped into families: conversation, memory, reasoning, tools, safety, gateway. A scenario whose category has no family is refused, never silently dropped.
- **Version** of a suite is a content hash of its scenario definitions (order-independent, stable across processes). Edit a scenario and the suite gets a new version; results of different versions are never compared.
- **Runs** are stored through the observatory store: an immutable `benchmark_run` event (per-item outcomes, conditions, commit) plus a `benchmark_pass_rate` metric sample linked to it.
- **Comparison** (`compare_runs`) only compares runs with the same suite, version, kind of evidence and models. It reports both pass rates with 95% Wilson intervals, calls a change "lower"/"higher" only when the intervals do not overlap, flags samples under 30 items, and names every item that flipped, including when the overall rate looks unchanged.
- **What it does not say:** nothing about live-model quality, and a difference is a correlation with whatever changed between the runs, not evidence of cause (attribution is OBS-04).

Not yet versioned (kept visible in `NOT_YET_VERSIONED`, with reasons): voice, latency, cost, resource use, live-model quality. Latency, cost and resource use are recorded per daily snapshot but have no fixed benchmark workload; voice is covered by pass/fail browser tests; live-model quality is the opt-in `python -m mia.brain.live_eval` smoke check, which is owner-run.

Usage: `run_all_families(commit=...)` returns one run per family; `record_run(store, run, day_index=...)` stores it; `list_runs(store, suite_id)` and `compare_runs(a, b)` read and compare. No CLI or dashboard wiring yet (OBS-06/07).
