# Observatory measurement definitions (MIA-OBS-01)

Code: `src/mia/observatory/measurements.py` (schema version 1). Enforced in `ObservatoryStore.record_metric`.

- **Missing is not zero.** A metric that could not be measured has no sample row. A row with value 0 means "measured, and it was 0". Each definition states `missing_when`.
- **Known metrics are strict.** Name, unit and range come from `METRIC_DEFINITIONS`: wrong unit, out-of-range or non-finite values are rejected (`ValueError`, HTTP 4xx through the API).
- **Unregistered names still work** (the API accepts ad-hoc metrics) but each sample is tagged `unregistered_metric` in its context so a report can tell vetted evidence from free-form numbers.
- **Versioned.** Every sample's context records `measurement_schema_version` and, for known metrics, `definition_version`, so a later change to a definition cannot silently reinterpret old samples.
- **Immutable.** `metric_samples` rows cannot be updated or deleted (SQLite triggers), matching baselines and daily snapshots. Samples written before this change are protected from now on, but they carry no definition version.

Registered: cpu_percent, memory_percent, test_pass_rate, fixed_benchmark_pass_rate, fixed_benchmark_duration_seconds, inference_cost_usd, inference_requests, inference_output_tokens, inference_latency_median_ms, inference_latency_p95_ms, baseline_checks_passed.

Fixed in this change: daily snapshots stored `fixed_benchmark_duration_seconds` with unit `percent`; it is now `seconds`. Samples already written with the wrong unit stay as they were (immutable); readers should treat any such pre-fix sample as seconds.

Not covered here (later M6 tasks): the Day 0 baseline format (OBS-02), scheduler behaviour (OBS-03), attribution (OBS-04), benchmark suites (OBS-05).

## Day 0 baseline manifest additions (MIA-OBS-02)

`capture_baseline_manifest` now also records, beside commit, configuration hash, environment and test results:

- `dependencies`: installed versions of every package `pyproject.toml` declares (core and optional). A declared package that is not installed is `null`; an unreadable `pyproject.toml` gives `{"available": false, "reason": ...}`. Dependencies are **not** part of `configuration_hash`, so a later upgrade is visible as drift instead of blocking daily snapshots.
- `resource_sample`: host CPU and memory percent read **before** the test run (so the tests do not skew it). An unavailable or out-of-range reading is `{"available": false, ...}`, never 0.
- `not_captured`: explicit reasons for what Day 0 does not measure: model inventory, inference latency, inference cost. Those start at the first daily snapshot that has real usage evidence.

`prepare_experiment` checks the shape of these sections when present and still accepts older manifests without them. The baseline itself stays immutable: it cannot be overwritten or deleted (triggers), and a second preparation while one is active is refused.
