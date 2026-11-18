# Mia Evolution Observatory

The observatory is a core project subsystem from the first experiment record. It must answer: **What changed from Day 0 to Day N, why did it change, and did the change measurably improve Mia?**

## Dashboard sections

1. **Experiment overview:** start date, elapsed day, pause/offline days, current status and source baseline.
2. **Growth trends:** benchmark quality, task success, reliability, autonomy and efficiency over time.
3. **Capability ledger:** created, reused, adapted, composed, improved, rejected, retired and rolled-back capabilities.
4. **SPS layer health:** activity and verification results for each of the ten layers.
5. **Code evolution:** version lineage, diffs, tests, activation and rollback history.
6. **Decision trace:** gap evidence, options considered, decision rationale and governance result.
7. **Resource and cost:** CPU/RAM use, model latency, token use, estimated/actual cost and provider quota state.
8. **Daily reports:** automatically generated summaries based only on stored events and benchmark results.

## Required graphs

- Multi-series line chart: benchmark scores by day and benchmark.
- Line chart: task success and reliability.
- Bar/stacked-bar chart: capability lifecycle events per day.
- Heatmap: ten SPS layers against days, showing events and validation outcomes.
- Resource charts: memory, CPU, latency and inference cost.
- Before/after comparison: selected Day A versus Day B with test-level differences.

Metrics with different units must not be plotted on the same unlabelled scale. A score should include its benchmark definition, version, environment and direction (higher/lower is better).

## Metric catalogue

| Metric | Type | Evidence |
|---|---|---|
| task_success_rate | Outcome | Fixed task suite successes / attempts |
| benchmark_score | Quality | Versioned benchmark run |
| regression_rate | Quality | Failed previously passing tests |
| reliability_rate | Operations | Successful completed tasks / eligible attempts |
| autonomy_completion_rate | Autonomy | Eligible tasks completed without user intervention |
| recovery_rate | Resilience | Recoveries completed without data loss |
| capability_reuse_rate | Reuse | Accepted reuse decisions / capability decisions |
| capability_count | Inventory | Active validated capabilities only |
| activation_failure_rate | Safety/quality | Failed activations / activation attempts |
| rollback_rate | Stability | Rollbacks / activated changes |
| p50_latency_ms / p95_latency_ms | Performance | Measured request timings |
| cpu_seconds / peak_ram_mb | Resource | Process/system measurements with source stated |
| estimated_cost_usd / actual_cost_usd | Cost | Provider usage/billing data, or zero for verified local use |

A larger capability count is not evidence of intelligence. A model's self-reported score is not a benchmark.

## Data integrity

- Store raw benchmark results and test outputs, not only aggregates.
- Use UTC timestamps and explicit Day 0-relative day indices.
- Store source commit, configuration hash, model IDs/versions and environment metadata.
- Never overwrite a baseline or a daily snapshot; append corrections as new events.
- Mark missing days as paused, unavailable or not run, not as zero-performance days.
- Export JSON/CSV and a final reproducibility manifest.
- Dashboard empty states must remain empty; never insert invented sample values.
