"""Versioned definitions for the metrics Mia records as experiment evidence (MIA-OBS-01).

Semantics, stated once:

* A **sample** is one measured value. A metric that could not be measured has **no sample**; it is
  never stored as 0. Zero means "measured, and it was zero".
* Known metrics must use their defined unit and range. Names that are not registered are still
  accepted (the API takes ad-hoc metrics) but are tagged ``unregistered_metric`` so reports can
  tell vetted evidence from free-form numbers.
* ``definition_version`` is stored with every sample so a later change to a definition never
  silently reinterprets old evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MEASUREMENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    unit: str
    description: str
    sampling: str                 # how/when one sample is taken
    source: str                   # where the value comes from
    minimum: float | None = None
    maximum: float | None = None
    missing_when: str = "the source was unavailable"
    version: int = 1


def _d(name: str, unit: str, description: str, sampling: str, source: str, *,
       minimum: float | None = 0.0, maximum: float | None = None,
       missing_when: str = "the source was unavailable") -> MetricDefinition:
    return MetricDefinition(name, unit, description, sampling, source, minimum, maximum, missing_when)


METRIC_DEFINITIONS: dict[str, MetricDefinition] = {m.name: m for m in (
    _d("cpu_percent", "percent", "Host CPU utilisation when the snapshot was taken.",
       "one reading per daily snapshot", "host resource probe", maximum=100.0),
    _d("memory_percent", "percent", "Host memory utilisation when the snapshot was taken.",
       "one reading per daily snapshot", "host resource probe", maximum=100.0),
    _d("test_pass_rate", "percent", "Passed / total of the verification test run.",
       "one value per daily snapshot", "pytest JUnit report", maximum=100.0,
       missing_when="no tests ran (the rate is undefined, not 0)"),
    _d("fixed_benchmark_pass_rate", "percent", "Passed / total of the fixed benchmark tests.",
       "one value per daily snapshot", "pytest JUnit report", maximum=100.0,
       missing_when="no benchmark tests ran (the rate is undefined, not 0)"),
    _d("fixed_benchmark_duration_seconds", "seconds", "Wall time of the fixed benchmark run.",
       "one value per daily snapshot", "pytest JUnit report"),
    _d("inference_cost_usd", "usd", "Inference spend in the snapshot window.",
       "one value per daily snapshot", "usage ledger", missing_when="the ledger is unavailable"),
    _d("inference_requests", "count", "Inference requests in the snapshot window.",
       "one value per daily snapshot", "usage ledger", missing_when="the ledger is unavailable"),
    _d("inference_output_tokens", "tokens", "Output tokens in the snapshot window.",
       "one value per daily snapshot", "usage ledger", missing_when="the ledger is unavailable"),
    _d("inference_latency_median_ms", "ms", "Median total request latency in the window.",
       "one value per daily snapshot", "usage ledger",
       missing_when="no request had a measured latency"),
    _d("inference_latency_p95_ms", "ms", "95th percentile total request latency in the window.",
       "one value per daily snapshot", "usage ledger",
       missing_when="no request had a measured latency"),
    _d("baseline_checks_passed", "checks", "Foundation baseline checks that passed.",
       "one value per baseline run", "foundation benchmark"),
)}


def validate_sample(metric_name: str, value: float, unit: str) -> dict[str, Any]:
    """Reject impossible values; return the metadata to store beside the sample."""
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("metric value must be a finite number")
    definition = METRIC_DEFINITIONS.get(metric_name)
    if definition is None:
        return {"measurement_schema_version": MEASUREMENT_SCHEMA_VERSION, "unregistered_metric": True}
    if unit != definition.unit:
        raise ValueError(f"metric {metric_name} is measured in {definition.unit}, not {unit}")
    if definition.minimum is not None and number < definition.minimum:
        raise ValueError(f"metric {metric_name} cannot be below {definition.minimum:g}")
    if definition.maximum is not None and number > definition.maximum:
        raise ValueError(f"metric {metric_name} cannot be above {definition.maximum:g}")
    return {"measurement_schema_version": MEASUREMENT_SCHEMA_VERSION,
            "definition_version": definition.version}
