"""Gateway-side adapter for advisory judge models (MIA-EVAL-03a).

``mia.brain.judges.run_judges`` takes an injected ``call(candidate, request) -> str``.
This module builds that callable on top of the normal :class:`ModelGateway`, so a
judge call gets exactly the same protections as any other completion: provider
health/backoff, credential handling, and atomic paid-budget reservations.

Guarantees:

* **Pinned candidate:** the call is made with ``[candidate]`` only. If that model fails
  the call raises; it never silently falls back to a different (possibly remote or
  paid) model than the one the judge policy selected.
* **Text only:** tools and any JSON schema are stripped, so a judge cannot trigger tool
  calls. A reply that contains tool calls or no text raises.
* **Route policy applies:** paid routes stay blocked unless the current route policy
  allows them and the price is known (the gateway enforces this, not this module).
* **No new data path:** the reviewed text is already inside ``request``; nothing extra
  is logged or persisted here. Usage can be recorded via the optional ``on_result`` hook.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from .contracts import ModelCandidate, ModelRequest, RoutePolicy
from .service import ModelGateway

# Structurally identical to mia.brain.judges.JudgeCall (kept local to avoid gateway -> brain imports).
JudgeCall = Callable[[ModelCandidate, ModelRequest], Awaitable[str]]


def make_judge_call(
    gateway: ModelGateway,
    route_policy: Callable[[], RoutePolicy] = RoutePolicy,
    *,
    on_result: Callable[[Any, ModelRequest], None] | None = None,
) -> JudgeCall:
    """Return a ``JudgeCall`` that pins one candidate through the normal gateway."""

    async def call(candidate: ModelCandidate, request: ModelRequest) -> str:
        pinned = replace(request, tools=(), json_schema=None)
        result = await gateway.complete(pinned, [candidate], route_policy())
        if on_result is not None:
            try:
                on_result(result, pinned)
            except Exception:  # accounting hooks must never break a judge call
                pass
        if result.tool_calls:
            raise RuntimeError("judge model returned tool calls; not accepted")
        text = (result.content or "").strip()
        if not text:
            raise RuntimeError("judge model returned no text")
        return text

    return call
