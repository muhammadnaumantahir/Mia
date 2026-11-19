"""Controlled memory capture from the owner's chat messages.

Pipeline for every user message:

1. Settings gate: memory off, or a temporary conversation, captures nothing.
2. Explicit commands ("remember ...", "forget ...", "update what you remember ...",
   "my preferred approach is ...") are handled first and always honoured while
   memory is enabled.
3. Otherwise, if automatic capture is on, sentences are screened. Questions, hedged
   or hypothetical statements, "right now" context, sensitive personal attributes
   and credentials are never captured automatically.
4. Each candidate is classified, then checked for duplicates and conflicts against
   existing memories.
5. The capture mode decides the outcome: ``approval`` queues a pending suggestion;
   ``auto`` saves only high-confidence, conflict-free candidates and queues the
   rest. Automatic capture NEVER overrides an existing memory.
6. Saved records carry provenance (``owner_provided`` for explicit instructions,
   ``observed`` for automatic capture, never owner-confirmed), the source message and
   conversation, and an audit entry.

Only the owner's own messages are captured. Assistant output is never treated as a
user fact. This is heuristic pattern matching, deliberately conservative: it will
miss things rather than invent them. Conflict detection catches records on the same
subject or with high wording overlap; contradictions phrased very differently are not
detected.
"""

from __future__ import annotations

import re
from typing import Any

from mia.memory.safety import find_secret
from mia.memory.structured import MAX_BODY, MAX_TITLE, RecordNotFoundError

DUPLICATE_SIMILARITY = 0.8
CONFLICT_SIMILARITY = 0.5
MAX_COMPARE = 200
MAX_SENTENCE = 300
MIN_SENTENCE_WORDS = 4

_STOPWORDS = frozenset(
    "the and for with that this from have has had are was were will would can could "
    "you your our their his her its but not all any been being into onto than then "
    "them they what when where which who whom whose about over under also just only "
    "very more most some such out too".split()
)

_REMEMBER = re.compile(
    r"^\s*(?:please\s+)?(?:remember|don'?t\s+forget|do\s+not\s+forget|keep\s+in\s+mind|"
    r"make\s+a\s+note|note\s+that)\b\s*(?:that|this|:|,|-)?\s*(?P<body>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_FORGET = re.compile(
    r"^\s*(?:please\s+)?(?:forget|stop\s+remembering)\b\s*"
    r"(?:about|that|what\s+(?:i|you)\s+(?:said|know|remember)\s+about|everything\s+about)?\s*"
    r"(?P<body>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_UPDATE = re.compile(
    r"^\s*(?:please\s+)?update\s+(?:what\s+you\s+remember\s+about|your\s+memory\s+(?:of|about)|"
    r"the\s+memory\s+about)\s+(?P<topic>.+?)\s*(?::|\s-\s|\s—\s|\bto\b|\bwith\b)\s*(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_MY_PREFERRED = re.compile(r"^\s*my\s+(?:preferred|favou?rite)\s+.+\s+is\s+.+$", re.IGNORECASE)
_GENERIC_REF = re.compile(r"^(?:this|that|it|this\s+one|the\s+above|everything)?\s*[.!]*$", re.IGNORECASE)

_UNCERTAIN = re.compile(
    r"\b(?:maybe|might|perhaps|probably|possibly|i\s+think|i\s+guess|i\s+suppose|not\s+sure|"
    r"could\s+be|hypothetically|what\s+if|supposing|if\s+i|if\s+we|i\s+wonder|should\s+i|"
    r"would\s+it)\b",
    re.IGNORECASE,
)
_TEMPORARY = re.compile(
    r"\b(?:right\s+now|at\s+the\s+moment|for\s+now|just\s+for\s+today|for\s+today|this\s+time|"
    r"tonight|this\s+session|temporarily)\b",
    re.IGNORECASE,
)
_SENSITIVE = re.compile(
    r"\b(?:religio\w*|muslim|christian|hindu|jewish|buddhist|atheist|faith|politic\w*|"
    r"democrat|republican|vote[ds]?|sexual\w*|gay|lesbian|bisexual|transgender|"
    r"orientation|ethnic\w*|race|racial|immigra\w*|visa|citizenship|union\s+member|"
    r"health|diagnos\w*|disease|illness|cancer|diabet\w*|medication|prescri\w*|"
    r"depress\w*|anxiety|disorder|therapy|pregnan\w*|disabilit\w*|criminal|convict\w*|"
    r"arrest\w*|salary|income|debt|ssn|passport)\b",
    re.IGNORECASE,
)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)

# (pattern, record_type, base confidence, subject group name or None)
_AUTO_PATTERNS: tuple[tuple[re.Pattern[str], str, float, str | None], ...] = (
    (re.compile(r"\bmy\s+(?:favou?rite|preferred)\s+(?P<subject>[\w\s-]{2,40}?)\s+is\s+(?P<value>.+)$", re.I),
     "preference", 0.8, "subject"),
    (re.compile(r"\bi\s+(?:always|usually|generally|never)\s+(?P<value>.+)$", re.I),
     "preference", 0.7, None),
    (re.compile(r"\bi\s+(?:prefer|like\s+to\s+use)\s+(?P<value>.+)$", re.I),
     "preference", 0.7, None),
    (re.compile(r"\b(?:we|i)\s+(?:have\s+)?(?:decided|agreed|chose|settled\s+on)\s+(?:to\s+|on\s+)?(?P<value>.+)$", re.I),
     "decision", 0.75, None),
    (re.compile(r"\b(?:we|i)(?:'ll|\s+will)\s+go\s+with\s+(?P<value>.+)$|\b(?:we|i)\s+are\s+going\s+with\s+(?P<value2>.+)$", re.I),
     "decision", 0.7, None),
    (re.compile(r"\bmy\s+(?:goal|aim|target)\s+is\s+(?:to\s+)?(?P<value>.+)$", re.I),
     "goal", 0.75, None),
    (re.compile(r"\bi\s+(?:plan|intend|aim)\s+to\s+(?P<value>.+)$", re.I),
     "goal", 0.6, None),
    (re.compile(r"\bi(?:'m|\s+am)\s+(?:working\s+on|building|developing)\s+(?P<value>.+)$", re.I),
     "project", 0.65, None),
    (re.compile(r"\bmy\s+(?P<subject>name|role|job\s+title|company|team|timezone|time\s+zone|"
                r"preferred\s+language|city)\s+is\s+(?P<value>.+)$", re.I),
     "fact", 0.8, "subject"),
)

_TYPE_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:decid\w*|decision|we\s+chose|going\s+with|agreed\s+(?:to|on))\b", re.I), "decision"),
    (re.compile(r"\b(?:lesson|learn(?:ed|t)|mistake|next\s+time|root\s+cause)\b", re.I), "lesson"),
    (re.compile(r"\b(?:goal|aim\s+to|plan\s+to|target|deadline)\b", re.I), "goal"),
    (re.compile(r"\b(?:prefer\w*|favou?rite|always\s+use|usually|i\s+like\s+to)\b", re.I), "preference"),
    (re.compile(r"\b(?:project|milestone|requirement|feature|repo(?:sitory)?|working\s+on|building)\b", re.I), "project"),
    (re.compile(r"\b(?:happened|met\s+with|yesterday|last\s+(?:week|month)|we\s+(?:discussed|talked))\b", re.I), "episode"),
    (re.compile(r"\bmy\s+(?:friend|colleague|manager|brother|sister|wife|husband|partner|client|mentor)\b", re.I), "entity"),
)

_IMPORTANCE = {
    "decision": 0.75, "goal": 0.7, "lesson": 0.65, "preference": 0.6,
    "project": 0.6, "entity": 0.5, "episode": 0.45, "fact": 0.5,
}


def tokens(text: str) -> set[str]:
    return {
        word for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) >= 3 and word not in _STOPWORDS
    }


def similarity(a: str, b: str) -> float:
    left, right = tokens(a), tokens(b)
    if not left or not right:
        return 1.0 if a.strip().lower() == b.strip().lower() else 0.0
    return len(left & right) / len(left | right)


def classify(text: str) -> str:
    for pattern, record_type in _TYPE_HINTS:
        if pattern.search(text):
            return record_type
    return "fact"


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _title_for(subject: str | None, text: str) -> str:
    if subject:
        return _clip(subject.strip().capitalize(), MAX_TITLE)
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    return _clip(" ".join(words[:8]).rstrip(".,;:!?"), 80) or "Memory"


def _clean_body(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def screen_sentence(sentence: str) -> str | None:
    """Return a skip reason if a sentence must not be captured automatically."""
    if "?" in sentence:
        return "question"
    if _UNCERTAIN.search(sentence):
        return "uncertain"
    if _TEMPORARY.search(sentence):
        return "temporary_context"
    if _SENSITIVE.search(sentence):
        return "sensitive_attribute"
    if find_secret(sentence):
        return "credential"
    return None


class CaptureService:
    """Applies the capture pipeline against a MemoryStore."""

    def __init__(self, memory: Any) -> None:
        self.memory = memory

    # ------------------------------------------------------------------ public

    def process_message(
        self, conversation_id: str, message_id: str, text: str
    ) -> dict[str, Any]:
        """Process one user message. Never raises for content problems."""
        result = self._empty()
        settings = self.memory.settings.get_all()
        body = _CODE_FENCE.sub(" ", text or "").strip()
        if not body:
            return result

        forget = _FORGET.match(body)
        if forget and len(body) < 400:
            result["processed"] = True
            result["explicit"] = True
            return self._forget(forget.group("body"), result)

        if not settings["memory_enabled"]:
            result["skipped"].append({"reason": "memory_disabled"})
            return result
        if self.memory.is_conversation_temporary(conversation_id):
            result["skipped"].append({"reason": "temporary_conversation"})
            return result
        if self.memory.structured.find_by_message(message_id):
            result["skipped"].append({"reason": "already_processed"})
            return result

        origin_base = {"conversation_id": conversation_id, "message_id": message_id}
        update = _UPDATE.match(body)
        if update:
            result["processed"] = result["explicit"] = True
            return self._update(update.group("topic"), update.group("body"), origin_base, result)

        remember = _REMEMBER.match(body)
        if remember and len(body) < 4000:
            result["processed"] = result["explicit"] = True
            content = _clean_body(remember.group("body"))
            if _GENERIC_REF.match(content):
                content = self._previous_user_message(conversation_id, message_id)
                if not content:
                    result["needs_clarification"] = (
                        "I am not sure what to remember. Tell me the fact, for example "
                        "'Remember that I prefer short answers.'"
                    )
                    return result
            self.remember(content, origin_base=origin_base, result=result, explicit_text=True)
            return result

        if _MY_PREFERRED.match(body) and len(body) < 400:
            result["processed"] = result["explicit"] = True
            self.remember(_clean_body(body), origin_base=origin_base, result=result,
                          explicit_text=True)
            return result

        if settings["capture_mode"] == "off":
            result["skipped"].append({"reason": "capture_off"})
            return result
        result["processed"] = True
        self._automatic(body, settings, origin_base, result)
        return result

    def remember(
        self,
        content: str,
        *,
        record_type: str | None = None,
        origin_base: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        explicit_text: bool = False,
    ) -> dict[str, Any]:
        """Explicit save. The owner asked, so it is owner_provided and active."""
        result = result if result is not None else self._empty()
        result["processed"] = result["explicit"] = True
        content = _clean_body(content)
        if not content:
            result["needs_clarification"] = "There was nothing to remember."
            return result
        if len(content) > MAX_BODY:
            result["skipped"].append({"reason": "too_long"})
            return result
        if find_secret(content):
            result["skipped"].append({"reason": "credential"})
            return result
        rtype = record_type or classify(content)
        subject = self._subject(content)
        title = _title_for(subject, re.sub(r"^(?:that|this)\s+", "", content, flags=re.I))
        origin = {**(origin_base or {}), "capture": "explicit"}
        dup, conflicts, rejected = self._compare(rtype, title, content)
        if dup:
            result["skipped"].append({"reason": "duplicate", "record_id": dup["record_id"],
                                      "title": dup["title"]})
            return result
        same_subject = [c for c in conflicts if c["title_key_match"]]
        if len(same_subject) == 1 and same_subject[0]["type"] == rtype:
            target = same_subject[0]
            new_id, _ = self.memory.structured.supersede(
                target["record_id"], title=title, body=content, actor="capture",
            )
            result["superseded"].append({"record_id": new_id, "replaces": target["record_id"],
                                         "title": title})
            return result
        try:
            record_id, _ = self.memory.structured.add(
                record_type=rtype, title=title, body=content, provenance="owner_provided",
                importance=min(1.0, _IMPORTANCE.get(rtype, 0.5) + 0.1), origin=origin,
                actor="capture",
            )
        except ValueError as exc:
            result["skipped"].append({"reason": "invalid", "detail": str(exc)})
            return result
        result["saved"].append({"record_id": record_id, "title": title, "record_type": rtype,
                                "status": "active"})
        if conflicts:
            result["conflicts"] = [c["record_id"] for c in conflicts]
        return result

    # ----------------------------------------------------------------- explicit

    def _forget(self, query: str, result: dict[str, Any]) -> dict[str, Any]:
        query = _clean_body(query).rstrip(".!")
        if not query or _GENERIC_REF.match(query):
            result["needs_clarification"] = (
                "Tell me what to forget, for example 'Forget what I said about the billing database.'"
            )
            return result
        matches = self._find_matches(query)
        if not matches:
            result["skipped"].append({"reason": "nothing_found"})
            return result
        if len(matches) > 1:
            result["needs_clarification"] = (
                "More than one memory matches; nothing was deleted. Tell me which: "
                + "; ".join(f"'{m['title']}'" for m in matches[:5])
            )
            result["candidates"] = [{"record_id": m["record_id"], "title": m["title"]}
                                    for m in matches[:5]]
            return result
        target = matches[0]
        self.memory.structured.delete(target["record_id"], actor="owner")
        result["forgotten"].append({"record_id": target["record_id"], "title": target["title"]})
        return result

    def _update(
        self, topic: str, new_text: str, origin_base: dict[str, Any], result: dict[str, Any]
    ) -> dict[str, Any]:
        new_text = _clean_body(new_text)
        if find_secret(new_text):
            result["skipped"].append({"reason": "credential"})
            return result
        matches = self._find_matches(_clean_body(topic))
        if len(matches) > 1:
            result["needs_clarification"] = (
                "More than one memory matches; nothing was changed. Tell me which: "
                + "; ".join(f"'{m['title']}'" for m in matches[:5])
            )
            result["candidates"] = [{"record_id": m["record_id"], "title": m["title"]}
                                    for m in matches[:5]]
            return result
        if not matches:
            return self.remember(new_text, origin_base=origin_base, result=result)
        target = matches[0]
        try:
            new_id, _ = self.memory.structured.supersede(
                target["record_id"], body=new_text, actor="capture",
            )
        except (ValueError, RecordNotFoundError) as exc:
            result["skipped"].append({"reason": "invalid", "detail": str(exc)})
            return result
        result["superseded"].append({"record_id": new_id, "replaces": target["record_id"],
                                     "title": target["title"]})
        return result

    # --------------------------------------------------------------- automatic

    def _automatic(
        self, body: str, settings: dict[str, Any], origin_base: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        seen: set[str] = set()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
            sentence = _clean_body(sentence)
            if not sentence or len(sentence.split()) < MIN_SENTENCE_WORDS:
                continue
            if len(sentence) > MAX_SENTENCE:
                continue
            candidate = self._match_auto(sentence)
            if candidate is None:
                continue
            reason = screen_sentence(sentence)
            if reason:
                result["skipped"].append({"reason": reason})
                continue
            rtype, confidence, subject = candidate
            title = _title_for(subject, sentence)
            key = f"{rtype}:{title.lower()}:{sentence.lower()}"
            if key in seen:
                continue
            seen.add(key)
            dup, conflicts, rejected = self._compare(rtype, title, sentence)
            if dup:
                result["skipped"].append({"reason": "duplicate", "record_id": dup["record_id"]})
                continue
            if rejected:
                result["skipped"].append({"reason": "previously_rejected"})
                continue
            auto_save = (
                settings["capture_mode"] == "auto"
                and confidence >= settings["auto_save_min_confidence"]
                and not conflicts
            )
            origin = {**origin_base, "capture": "auto",
                      "conflicts_with": [c["record_id"] for c in conflicts]}
            try:
                record_id, _ = self.memory.structured.add(
                    record_type=rtype, title=title, body=sentence, provenance="observed",
                    confidence=confidence, evidence=[{"type": "message", "ref": origin_base["message_id"]}],
                    status="active" if auto_save else "pending", importance=_IMPORTANCE.get(rtype, 0.5),
                    origin=origin, actor="capture",
                )
            except ValueError as exc:
                result["skipped"].append({"reason": "invalid", "detail": str(exc)})
                continue
            entry = {"record_id": record_id, "title": title, "record_type": rtype,
                     "status": "active" if auto_save else "pending", "confidence": confidence}
            (result["saved"] if auto_save else result["pending"]).append(entry)
            if conflicts:
                result["conflicts"].extend(c["record_id"] for c in conflicts)

    @staticmethod
    def _match_auto(sentence: str) -> tuple[str, float, str | None] | None:
        for pattern, rtype, confidence, subject_group in _AUTO_PATTERNS:
            match = pattern.search(sentence)
            if match:
                subject = match.groupdict().get(subject_group) if subject_group else None
                return rtype, confidence, subject
        return None

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"processed": False, "explicit": False, "saved": [], "pending": [],
                "superseded": [], "forgotten": [], "skipped": [], "conflicts": [],
                "needs_clarification": None}

    @staticmethod
    def _subject(content: str) -> str | None:
        match = re.match(
            r"^(?:that\s+)?my\s+(?P<s>(?:preferred|favou?rite)\s+[\w\s-]{2,40}?|name|role|"
            r"job\s+title|company|team|timezone|time\s+zone|city)\s+is\s+", content, re.I)
        return match.group("s") if match else None

    def _previous_user_message(self, conversation_id: str, message_id: str) -> str:
        messages = self.memory.get_messages(conversation_id, limit=6)
        previous = [m for m in messages if m["role"] == "user" and m["message_id"] != message_id]
        return _clean_body(previous[-1]["content"]) if previous else ""

    def _compare(
        self, record_type: str, title: str, text: str
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
        """Return (duplicate, conflicts, previously_rejected) against existing records."""
        from mia.memory.structured import normalise_title

        title_key = normalise_title(title)
        dup: dict[str, Any] | None = None
        conflicts: list[dict[str, Any]] = []
        rejected = False
        for status in ("active", "pending", "rejected"):
            for rec in self.memory.structured.list(
                record_type=record_type, status=status, limit=MAX_COMPARE
            ):
                sim = similarity(text, rec["body"])
                same_title = normalise_title(rec["title"]) == title_key
                if sim >= DUPLICATE_SIMILARITY:
                    if status == "rejected":
                        rejected = True
                    elif dup is None:
                        dup = rec
                    continue
                if status == "active" and (same_title or sim >= CONFLICT_SIMILARITY):
                    conflicts.append({"record_id": rec["record_id"], "type": rec["record_type"],
                                      "title_key_match": same_title})
        return dup, conflicts, rejected

    def _find_matches(self, query: str) -> list[dict[str, Any]]:
        found = self.memory.retrieve_records(query, limit=6)
        records = []
        for hit in found:
            record = self.memory.structured.get(hit["record_id"])
            if record:
                records.append(record)
        if not records:
            records = [
                r for r in self.memory.structured.list(query=query, limit=6)
                if r["status"] in {"active", "completed", "pending", "outdated", "archived"}
            ]
        return records
