"""Capture explicit curiosity from the owner's own chat messages (deterministic, no model).

Only phrases that clearly invite later exploration are captured ("I wonder ...",
"I'm curious about ...", "look into X when you have time"). Ordinary questions are
answered in the chat and never queued. Only the owner's message is examined, never
tool output or web text, and the result is an owner-visible, deletable backlog item
that quotes the owner's sentence as its reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mia.life.store import LifeSettings, LifeStore

MAX_MESSAGE = 4000
MAX_OPEN_CONVERSATION_ITEMS = 20
_WONDER = re.compile(
    r"\b(?:i\s+wonder(?:\s+about)?"
    r"|i\s*(?:am|'m|’m)\s+curious(?:\s+about)?"
    r"|i(?:'d|’d|\s+would)\s+(?:love|like)\s+to\s+(?:know|understand)(?:\s+(?:more\s+)?about)?)\s+"
    r"(?P<topic>[^.!?\n]+)",
    re.IGNORECASE,
)
_VERB = r"(?:look\s+into|research|find\s+out(?:\s+(?:about|more\s+about))?|investigate|dig\s+into)"
_WHEN = r"(?:later|sometime|when\s+you\s+have\s+(?:time|a\s+moment)|in\s+the\s+background|on\s+your\s+own|in\s+your\s+spare\s+time|when\s+idle)"
_DELEGATE_AFTER = re.compile(rf"\b{_VERB}\s+(?P<topic>[^.!?\n]+?)\s+(?:{_WHEN})\b", re.IGNORECASE)
_DELEGATE_BEFORE = re.compile(rf"\b{_WHEN},?\s+(?:please\s+)?{_VERB}\s+(?P<topic>[^.!?\n]+)", re.IGNORECASE)
_SENSITIVE = re.compile(r"\b(?:password|passcode|api[ _-]?key|secret|token|credential)s?\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CuriosityCapture:
    title: str
    reason: str


def _clean(topic: str) -> str | None:
    topic = re.sub(r"\s+", " ", topic).strip(" ,;:-\"'")
    topic = re.sub(r"\s+for me$", "", topic, flags=re.IGNORECASE)
    if "http://" in topic.lower() or "https://" in topic.lower() or _SENSITIVE.search(topic):
        return None  # URLs must be added deliberately; secrets are never queued
    words = topic.split()
    if len(words) < 2 or not 8 <= len(topic) <= 200:
        return None
    return topic


def extract_curiosity(message: str) -> CuriosityCapture | None:
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", message[:MAX_MESSAGE]):
        for pattern in (_WONDER, _DELEGATE_AFTER, _DELEGATE_BEFORE):
            match = pattern.search(sentence)
            if match and (topic := _clean(match.group("topic"))):
                return CuriosityCapture(
                    title=f"Look into: {topic}",
                    reason=f'You said in conversation: "{sentence.strip()[:300]}"',
                )
    return None


def capture_from_message(
    store: LifeStore, message: str, conversation_id: str, settings: LifeSettings | None = None
) -> str | None:
    """Queue at most one curiosity item for this message; return its id when created."""
    settings = settings or store.get_settings()
    if not settings.conversation_capture_enabled or settings.emergency_stop:
        return None
    found = extract_curiosity(message)
    if found is None:
        return None
    open_items = [i for i in store.list_items(state="queued", limit=200) if i["origin"] == "conversation"]
    if len(open_items) >= MAX_OPEN_CONVERSATION_ITEMS:
        return None
    item_id, created = store.add_item(
        title=found.title, why_it_matters=found.reason, origin="conversation",
        source_ref={"type": "conversation", "ref": conversation_id},
        expected_value=0.6, novelty=0.5,
    )
    return item_id if created else None
