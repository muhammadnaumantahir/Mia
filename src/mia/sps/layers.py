"""Canonical, immutable definitions of the ten SPS layers."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LayerDefinition:
    layer_id: str
    number: int
    name: str
    responsibility: str


SPS_LAYERS: tuple[LayerDefinition, ...] = (
    LayerDefinition(
        "SPS-01", 1, "Software DNA Core",
        "Identity, design constraints and capability definitions.",
    ),
    LayerDefinition(
        "SPS-02", 2, "Governance Core",
        "Permissions, policy, risk, budgets and approvals.",
    ),
    LayerDefinition(
        "SPS-03", 3, "Cognitive Core",
        "Intent interpretation, reasoning, planning and decisions.",
    ),
    LayerDefinition(
        "SPS-04", 4, "Knowledge Core",
        "Research, documents, retrieval and source provenance.",
    ),
    LayerDefinition(
        "SPS-05", 5, "Experience Core",
        "Interaction history, task outcomes and experience records.",
    ),
    LayerDefinition(
        "SPS-06", 6, "Meta-Learning Core",
        "Evaluate outcomes and learn from trace evidence.",
    ),
    LayerDefinition(
        "SPS-07", 7, "Adaptation Core",
        "Adjust strategies, routing, workflows and compositions.",
    ),
    LayerDefinition(
        "SPS-08", 8, "Evolution Core",
        "Propose, create, improve, version and retire capabilities.",
    ),
    LayerDefinition(
        "SPS-09", 9, "Verification & Validation Core",
        "Checks, tests, security validation and benchmarks.",
    ),
    LayerDefinition(
        "SPS-10", 10, "Execution Core",
        "Controlled tool execution and task lifecycle.",
    ),
)


def get_layer(layer_id: str) -> LayerDefinition:
    """Return a canonical layer or raise KeyError for an unknown identifier."""
    for layer in SPS_LAYERS:
        if layer.layer_id == layer_id:
            return layer
    raise KeyError(f"Unknown SPS layer: {layer_id}")
