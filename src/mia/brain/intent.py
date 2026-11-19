"""Deterministic intent, reference and ambiguity analysis (SPS-03 Cognitive Core).

No model call is made here: simple requests must stay cheap. The analysis
chooses the task category, how much processing a request deserves, whether it
points back at earlier work, and whether it is too ambiguous or high-impact to
act on without asking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .capabilities import normalise_category

_GREETING = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|good (morning|evening|night)|yes|no)\b[\s!.?]*$",
    re.I,
)
_CONTINUE = re.compile(
    r"\b(continue|carry on|resume|pick up)\b.*\b(where|from)\b.*\b(stopped|left off|were|we)\b"
    r"|\bcontinue (with )?(that|this|it)\b|\bwhere we (stopped|left off)\b",
    re.I,
)
_PAST_ISSUE = re.compile(
    r"\b(same|that|the) (issue|problem|bug|error)\b.*\b(we|you|i)\b.*\b(discussed|talked|mentioned|saw)"
    r"|\b(discussed|talked about|mentioned) (yesterday|earlier|before|last)\b",
    re.I,
)
_PAST_DECISION = re.compile(
    r"\b(previous|earlier|last|prior) (decision|choice|plan|approach|conclusion)\b"
    r"|\bcompare .* (with|to) .*\b(previous|earlier|our)\b",
    re.I,
)
_RECALL = re.compile(
    r"\b(did|do|have) (we|i|you) (choose|chose|decide|decided|pick|picked|agree|agreed|say|said|use|used|"
    r"discuss|discussed|talk|talked|settle|settled)\b|\bwhat (did|was) (we|i|you|our)\b.*\b(decid|choos|agre|pick)",
    re.I,
)
_PREFERENCE = re.compile(
    r"\bmy (usual|normal|preferred|standard|default)\b|\bas i (usually|always) (like|do|prefer)\b", re.I
)
_DECOMPOSE = re.compile(
    r"\bbreak (this|that|it|the task)? ?(down )?(into|up)\b|\bsmaller steps\b|\bstep[- ]by[- ]step plan\b", re.I
)
_EXPLAIN_WHY = re.compile(
    r"\bwhy did you (select|choose|pick|use|decide)\b|\bexplain (why|your (choice|approach|reasoning))\b", re.I
)
_DEICTIC = re.compile(r"\b(it|this|that|these|those|the same|them)\b", re.I)
_PROJECT = re.compile(r"\b(my|the|our) (project|repo|repository|codebase|app|site|website)\b", re.I)

_CODING = re.compile(
    r"\b(code|function|class|bug|stack ?trace|exception|refactor|compile|unit test|python|javascript|"
    r"typescript|sql|regex|script|api endpoint|debug|implement)\b", re.I)
_RESEARCH = re.compile(r"\b(research|investigate|compare|find out|look up|sources?|literature|survey)\b", re.I)
_SUMMARY = re.compile(r"\b(summari[sz]e|tl;?dr|recap|condense|shorten)\b", re.I)
_CLASSIFY = re.compile(r"\b(classify|categori[sz]e|label|is this (spam|positive|negative)|sentiment)\b", re.I)
_EXTRACT = re.compile(r"\b(extract|parse|pull out|convert to json|as json|fields? from)\b", re.I)
_PLAN = re.compile(r"\b(plan|roadmap|strategy|schedule|steps? to|how (do|should) (i|we) (go about|approach))\b", re.I)
_REASON = re.compile(
    r"\b(why|analy[sz]e|trade-?offs?|prove|derive|evaluate|optimi[sz]e|root cause|pros and cons)\b", re.I)
_TOOL = re.compile(
    r"\b(open|launch|read (the )?file|list (the )?files|search (my|the) (files|documents|knowledge)|"
    r"browse|navigate to|click|inspect)\b", re.I)
_MULTI = re.compile(r"\b(and then|then|after that|first|second|finally|also|as well as)\b", re.I)
_HIGH_IMPACT = re.compile(
    r"\b(delete|remove|erase|wipe|format|uninstall|install|send|email|post|publish|pay|purchase|buy|"
    r"transfer|shut ?down|restart|overwrite|reset|disable|deploy|push)\b", re.I)
_CONCISE = re.compile(r"\b(briefly|short answer|concise|in one line|tl;?dr|quick)\b", re.I)
_DETAILED = re.compile(r"\b(in detail|detailed|thorough|comprehensive|deep dive|step by step)\b", re.I)

_FILLER = {
    "continue", "where", "stopped", "yesterday", "discussed", "same", "issue", "previous", "decision",
    "usual", "preferences", "development", "break", "task", "smaller", "steps", "explain", "approach",
    "selected", "compare", "with", "our", "this", "that", "use", "fix", "the", "my", "we",
}


@dataclass(frozen=True, slots=True)
class IntentAnalysis:
    category: str
    complexity: str  # "simple" | "moderate" | "complex"
    references: tuple[str, ...] = ()
    needs_memory: bool = False
    needs_clarification: bool = False
    clarification: str = ""
    high_impact: bool = False
    style: str = "balanced"  # "concise" | "balanced" | "detailed" | "explanatory"
    wants_plan: bool = False
    signals: tuple[str, ...] = ()
    memory_query: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "complexity": self.complexity,
            "references": list(self.references),
            "needs_memory": self.needs_memory,
            "needs_clarification": self.needs_clarification,
            "high_impact": self.high_impact,
            "style": self.style,
            "wants_plan": self.wants_plan,
            "signals": list(self.signals),
        }


def _category(text: str) -> tuple[str, list[str]]:
    order = (
        ("classification", _CLASSIFY), ("summarization", _SUMMARY), ("coding", _CODING),
        ("extraction", _EXTRACT), ("tool", _TOOL), ("planning", _PLAN), ("research", _RESEARCH),
        ("reasoning", _REASON),
    )
    hits = [(name, rx) for name, rx in order if rx.search(text)]
    if not hits:
        return "conversation", []
    return hits[0][0], [name for name, _ in hits]


def analyze_intent(
    prompt: str,
    *,
    task_hint: str | None = None,
    has_prior_context: bool = False,
    active_task: bool = False,
) -> IntentAnalysis:
    text = prompt.strip()
    signals: list[str] = []
    refs: list[str] = []

    if _GREETING.match(text):
        return IntentAnalysis("simple", "simple", style="concise", signals=("greeting",))

    if _CONTINUE.search(text):
        refs.append("continuation")
    if _PAST_ISSUE.search(text):
        refs.append("past_issue")
    if _PAST_DECISION.search(text):
        refs.append("past_decision")
    if _RECALL.search(text):
        refs.append("recall")
    if _PREFERENCE.search(text):
        refs.append("preference")
    if _EXPLAIN_WHY.search(text):
        refs.append("explain_choice")
    decompose = bool(_DECOMPOSE.search(text))
    if decompose:
        refs.append("decompose_previous")

    category, hits = _category(text)
    hinted = normalise_category(task_hint) if task_hint else "conversation"
    if category == "conversation" and hinted != "conversation":
        category = hinted
    if refs and category == "conversation" and "explain_choice" in refs:
        category = "reasoning"
    if decompose:
        category = "planning"

    steps_markers = len(_MULTI.findall(text))
    long_prompt = len(text) > 600
    multi_part = steps_markers >= 2 or text.count("?") >= 3 or text.count("\n- ") >= 2
    if category in {"classification", "extraction"} and not (multi_part or long_prompt):
        complexity = "simple" if len(text) < 300 else "moderate"
    elif multi_part or long_prompt or (_PROJECT.search(text) and category in {"reasoning", "research", "planning", "tool"}):
        complexity = "complex"
    elif category in {"reasoning", "planning", "research", "coding"}:
        complexity = "moderate"
    elif len(text) < 80 and not refs:
        complexity = "simple"
    else:
        complexity = "moderate"
    if hits:
        signals.append("category:" + ",".join(hits[:3]))
    if multi_part:
        signals.append("multi_part")

    needs_memory = bool(refs) or bool(_PROJECT.search(text)) or complexity == "complex"
    high_impact = bool(_HIGH_IMPACT.search(text)) and category in {"tool", "conversation", "planning"}
    if _HIGH_IMPACT.search(text):
        signals.append("high_impact_verb")

    ambiguity = ""
    only_deictic = (
        _DEICTIC.search(text) is not None
        and len(text.split()) <= 6
        and not has_prior_context
        and not refs
        and not active_task
    )
    if only_deictic and category in {"coding", "tool", "reasoning", "conversation"}:
        ambiguity = "The request refers to something ('it'/'that') but there is no earlier context to resolve it."
    elif "continuation" in refs and not has_prior_context:
        ambiguity = "There is no earlier conversation or task to continue."
    elif high_impact and _DEICTIC.search(text) and not has_prior_context:
        ambiguity = "This could change something, and the target is not clear."

    style = "balanced"
    if _CONCISE.search(text):
        style = "concise"
    elif "explain_choice" in refs or re.search(r"\b(explain|why)\b", text, re.I):
        style = "explanatory"
    elif _DETAILED.search(text):
        style = "detailed"

    terms = [w for w in re.findall(r"[A-Za-z0-9_\-]{3,}", text) if w.lower() not in _FILLER]
    memory_query = " ".join(terms[:12]) if needs_memory else ""

    return IntentAnalysis(
        category=category,
        complexity=complexity,
        references=tuple(refs),
        needs_memory=needs_memory,
        needs_clarification=bool(ambiguity),
        clarification=ambiguity,
        high_impact=high_impact,
        style=style,
        wants_plan=complexity == "complex" or decompose,
        signals=tuple(signals),
        memory_query=memory_query,
    )
