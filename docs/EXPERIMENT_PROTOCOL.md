# Mia 30-day autonomous experiment protocol

## Purpose

Measure whether Mia develops useful, validated capabilities and whether its task performance, reliability, autonomy and efficiency improve from a fixed Day 0 baseline.

This is an experiment, not a requirement to generate code every day.

## Day 0 — immutable baseline

Capture:
- Git commit SHA and clean/dirty state;
- hashes of tracked source, configuration and test files;
- OS, CPU, RAM, storage and runtime versions;
- active model and provider IDs, quantization and inference settings;
- installed capability registry and permission profile;
- fixed benchmark and task-suite versions;
- baseline raw outputs, scores, failures and timing;
- safety policy, network settings and spend limits.

Create a read-only baseline manifest and export it outside the mutable workspace. Hash the manifest and archive it. Any later correction becomes a separately versioned record.

## Daily process

At each daily cycle:
1. Check stop conditions, resource budget and governance.
2. Read prior event traces and the current capability registry.
3. Run a small health and regression suite.
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

Include methods, environment, baseline manifest hash, all daily records, capability lineage, benchmark versions, raw and aggregated results, costs, failures, limitations and reproducible steps. Never claim improvement solely from source-code size, tool count or self-assessment.
