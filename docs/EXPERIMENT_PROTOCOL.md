# Mia 30-day autonomous experiment protocol

## Purpose

Measure whether Mia develops useful, validated capabilities and whether its task performance, reliability, autonomy and efficiency improve from a fixed Day 0 baseline.

This is an experiment, not a requirement to generate code every day.

## Day 0 — immutable baseline

Capture:
- Git commit SHA and clean/dirty state;
- hashes of tracked source, configuration and fixed benchmark files, plus an allowlisted hash of runtime policy settings (no credentials or raw private paths/URLs);
- OS, CPU, RAM, storage and runtime versions;
- active model and provider IDs, quantization and inference settings;
- installed capability registry and permission profile;
- the separately versioned fixed benchmark suite (`tests/benchmarks/test_foundation.py`) and the evolving full regression suite;
- baseline raw outputs, scores, failures and timing;
- safety policy, network settings and spend limits.

Create a read-only baseline manifest and export it outside the mutable workspace. Hash the manifest and archive it. Any later correction becomes a separately versioned record.

## Daily process

At each daily cycle:
1. Check stop conditions, resource budget and governance.
2. Read prior event traces and the current capability registry.
3. Run the unchanged fixed benchmark suite and the evolving full regression suite as separate commands. The fixed suite is fingerprinted in Day 0; changes to it require a new benchmark-suite version.
4. Identify evidence-backed capability gaps.
5. Evaluate reuse, adapt, improve, compose, create, defer or reject.
6. Generate candidate changes only when justified.
7. Test candidates in isolation.
8. Activate only validated candidates with required approval.
9. Record outcomes, resource use, diffs and rollback references.
10. Run the daily benchmark subset and produce an evidence-backed report.

## Planned research stages

- Days 1–5: gap discovery and low-risk corrections.
- Days 6–10: capability development and integration.
- Days 11–15: adaptation and experience analysis.
- Days 16–20: capability composition and reusable workflows.
- Days 21–25: reliability and efficiency improvements.
- Days 26–30: final evaluation and independent review.

These are themes, not forced release targets. Safe no-op days are legitimate results.

## Evaluation design

Keep a fixed benchmark suite and a separate evolving workload. Report both. Where practical, run the original Day 0 build against the same fixed tasks as a control to separate Mia's code changes from changes in models, prompts, data or task difficulty.

Report:
- fixed benchmark outcomes and elapsed time separately from full regression-suite counts and results;
- absolute score and change from baseline;
- per-task success/failure and confidence/uncertainty;
- regression count and severity;
- successful versus rejected/rolled-back changes;
- reuse versus original code contributions;
- human interventions and approvals;
- inference cost, latency, CPU/RAM and downtime;
- missing/paused days and reasons.

## Stop conditions

Stop or pause evolution on data loss, credential exposure, policy bypass, uncontained process, repeated benchmark regression, resource exhaustion, unknown high-impact side effects, or an owner-requested stop. The interactive assistant may remain available if safe.

## Final report

Include methods, environment, baseline manifest hash, all daily records, capability lineage, fixed benchmark and full regression results separately, costs, failures, limitations and reproducible steps. Never claim improvement solely from source-code size, tool count or self-assessment.

## Daily activity evidence (implemented)

Each daily snapshot also stores an `activity` block, covered by the same snapshot hash as the rest of the evidence. It is read-only and holds counts of each store's own events for the window since the previous daily snapshot (or the experiment start for Day 1; the Day 0 baseline is not part of the window):

- inference usage from the usage ledger: requests, provider-reported or estimated tokens, USD cost, paid and non-local request counts;
- capability transitions by status (proposed, validated, rejected, registered, active, rolled_back, retired) and the number of owner-approved promotion or rollback actions;
- task transitions by status, owner actions, and restart and lease recoveries.

Rules: a source that is not configured or cannot be read is recorded as `available: false` with a reason and is listed in the report's `activity_totals.unavailable_sources`; it is never recorded as zero. These stores are not linked to experiment IDs, so the block says explicitly that the counts are window counts, not attribution. A quiet day is a real zero; a missing source is not. Inference cost, request and output-token counts are also written as daily metrics. The report adds `activity_totals`, summing only days with readable evidence and listing days without it.

Not covered yet: per-task success rates against a fixed task set, latency distributions, reuse-versus-original code attribution, downtime, and a control run of the Day 0 build.

