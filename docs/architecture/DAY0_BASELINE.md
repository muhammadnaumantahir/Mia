# Day 0 baseline capture

Mia never accepts a browser/client-supplied baseline manifest as Day 0 evidence.
Calling \`POST /api/experiments/prepare\` now asks the local Mia process to capture
the current Git commit, fingerprint the fixed benchmark/configuration files,
record the local OS/Python/hardware description, and execute
\`tests/benchmarks/test_foundation.py\` on the host running Mia.

Capture is refused when the process detects the GitHub Actions environment,
when Git is unavailable, or when tracked changes are uncommitted. Untracked
files are not included in the commit identity; the benchmark and configuration
files themselves are fingerprinted. The endpoint prepares an experiment only
when every test in
\`mia-foundation-v1\` passes, with zero failures, errors or skips. It records the
pytest exit status, counts, duration, output hashes and a bounded output tail
with the immutable baseline manifest. A Git checkout, Git executable and the
project's development dependencies (including pytest) are required.

The fixed suite is intentionally offline and deterministic: it checks the
ten-layer SPS contract, task approval/event lifecycle, read-only tool policy,
memory provenance/indexing, capability source/test hashes, and observatory
event/metric recording. It does not call an external model, require Docker, or
invent quality/growth scores. The Day 0 capture is local to the machine hosting
Mia; GitHub Actions data is not accepted as a substitute.

Example request:

\`\`\`http
POST /api/experiments/prepare
Content-Type: application/json

{"name":"Mia 30-day autonomous experiment","planned_days":30}
\`\`\`

Review the returned \`source_commit\`, \`environment\`, \`benchmark_suite_id\`
and \`baseline_results\` before explicitly starting the experiment. A failed
benchmark run leaves the experiment unprepared. Daily snapshots remain
append-only and must be collected from the same local installation under
comparable benchmark conditions.
