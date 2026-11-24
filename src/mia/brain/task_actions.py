"""Deterministic conversational task actions (MIA-LIFE-03, Brain half).

The Brain answers a clear task command itself, without a model call, by acting on the same
``memory.personal`` store that the task page and ``/api/personal/items`` use, so there is one
source of truth. English only; anything that is not an explicit command returns ``None`` and
the turn continues as normal chat.

Commands (all anchored; a casual sentence never triggers them):
  add a task: <title> [due YYYY-MM-DD[ HH:MM]] [high|low priority] [in project <name>]
  add <title> to my task list
  show/list my [open|overdue|done] tasks [in project <name>]   |   what are my open tasks
  mark task <title or id> done   |   complete task <title or id>
  set task <title or id> to high|normal|low priority
  delete task <title or id>                                   (needs confirmation)
  delete/clear my finished|completed|cancelled tasks          (needs confirmation)
  delete all tasks [in project <name>]                        (needs confirmation)
  mark all my overdue|open tasks done [in project <name>]     (needs confirmation)

Safety rules
- Every delete, and every change to more than one task, is only *requested*: Mia states exactly
  what would happen and waits for an explicit "yes". Any other message cancels it, it expires
  after ten minutes, and it is kept in memory only, so a restart can never run a stale request.
  At confirmation only the task ids shown at request time are touched, and only if they still
  match.
- Private tasks are invisible to chat: never listed, matched by title or id, or changed, because
  chat replies are stored in the conversation and may later be sent to a model provider.
- Temporary chats are read-only (listing is allowed; changes are refused).
- Dates are read in the owner's timezone; a date without a time means 23:59 that day.
- Text that looks like a credential is refused by the store and reported, never echoed.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from mia.memory.personal import DuplicateItemError

MAX_COMMAND_CHARS = 600
CONFIRM_TTL_SECONDS = 600
MAX_BULK = 200
SHOW = 8

_END = r"\s*[.!]*\s*$"
_PRE = r"^\s*(?:please\s+|can you\s+|could you\s+)?"
_PROJECT = r"(?:\s+(?:in|for)\s+project\s+(?P<proj>[^,;]{1,64}?))?"

_BULK_DELETE_FINISHED = re.compile(
    _PRE + r"(?:delete|remove|clear)\s+(?:all\s+)?(?:of\s+)?(?:my\s+)?"
    r"(?P<which>finished|completed|done|cancelled|canceled)\s+(?:tasks|todos|to-dos)" + _PROJECT + _END, re.I)
_BULK_DELETE_ALL = re.compile(
    _PRE + r"(?:delete|remove)\s+all\s+(?:of\s+)?(?:my\s+)?(?:tasks|todos|to-dos)" + _PROJECT + _END, re.I)
_BULK_COMPLETE = re.compile(
    _PRE + r"(?:mark|complete|finish)\s+all\s+(?:of\s+)?(?:my\s+)?(?P<which>overdue|open)\s+"
    r"(?:tasks|todos|to-dos)" + _PROJECT + r"(?:\s+(?:as\s+)?(?:done|complete|completed|finished))?" + _END, re.I)
_DELETE_ONE = re.compile(
    _PRE + r"(?:delete|remove)\s+(?:my\s+)?task\s+(?P<ref>(?!all\b).+?)" + _END, re.I)
_COMPLETE_TASK = re.compile(
    _PRE + r"(?:(?:mark|set)\s+(?:my\s+)?task\s+(?P<ref>.+?)\s+(?:as\s+)?(?:done|complete|completed|finished)"
    r"|(?:complete|finish)\s+(?:my\s+)?task\s+(?P<ref2>.+?))" + _END, re.I)
_COMPLETE_BARE = re.compile(
    _PRE + r"mark\s+(?P<ref>.+?)\s+as\s+(?:done|complete|completed|finished)" + _END, re.I)
_PRIORITY = re.compile(
    _PRE + r"(?:(?:set|make|change)\s+(?:the\s+)?(?:priority\s+of\s+)?task\s+(?P<ref>.+?)\s+(?:to|as)\s+"
    r"(?P<prio>high|low|normal)(?:\s+priority)?|make\s+task\s+(?P<ref2>.+?)\s+(?P<prio2>high|low|normal)"
    r"\s+priority)" + _END, re.I)
_ADD_A = re.compile(
    _PRE + r"(?:add|create|make)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:task|todo|to-do)\s*"
    r"(?::|-|called|named|titled|to)?\s*(?P<body>.+?)" + _END, re.I)
_ADD_B = re.compile(
    _PRE + r"add\s+(?P<body>.+?)\s+to\s+my\s+(?:tasks?|todos?|to-do)(?:\s+list)?" + _END, re.I)
_LIST_A = re.compile(
    _PRE + r"(?:show|list|display|give)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?(?:my\s+)?"
    r"(?P<which>open|overdue|finished|completed|done)?\s*(?:tasks|todos|to-dos|to-do list|task list)"
    + _PROJECT + _END, re.I)
_LIST_B = re.compile(
    r"^\s*what(?:'s|\s+is|\s+are)\s+(?:on\s+)?my\s+(?P<which>open|overdue)?\s*"
    r"(?:tasks|todos|to-dos|to-do list|task list)(?:\s+(?:right\s+now|today))?\s*\??\s*$", re.I)
_CONFIRM = re.compile(
    r"^\s*(?:yes|y|yep|yeah|confirm|confirmed|go ahead|do it|proceed|yes,?\s*(?:please|confirm|do it|delete|go ahead))"
    r"\s*[.!]*\s*$", re.I)
_CANCEL = re.compile(r"^\s*(?:no|n|nope|cancel|stop|never\s*mind|don't|do not)\s*[.!]*\s*$", re.I)
_DUE = re.compile(r"\s*\bdue\s+(?P<d>\d{4}-\d{2}-\d{2})(?:[ T](?P<t>\d{2}:\d{2}))?\b", re.I)
_PRIO_WORD = re.compile(r"\s*\b(?P<p>high|low|normal)\s+priority\b", re.I)
_TRAIL_PROJECT = re.compile(r"\s+(?:in|for)\s+project\s+(?P<proj>[^,;]{1,64})$", re.I)
_ID = re.compile(r"^[0-9a-f]{8,32}$")
_PRIORITY_VALUE = {"high": 1, "normal": 2, "low": 3}
_PRIORITY_NAME = {1: "high", 2: "normal", 3: "low"}


@dataclass(slots=True)
class _Pending:
    op: str  # delete | complete
    ids: list[str]
    expires: float
    description: str


class TaskActions:
    """Callable hook for the Brain: ``handle(conversation_id, message_id, prompt)``."""

    def __init__(self, memory: Any, *, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.memory = memory
        self._clock = clock
        self._now = now
        self._pending: dict[str, _Pending] = {}

    # ------------------------------------------------------------------ entry

    def handle(self, conversation_id: str, message_id: str, prompt: str) -> dict[str, Any] | None:
        text = (prompt or "").strip()
        if not text or len(text) > MAX_COMMAND_CHARS:
            return None
        pending = self._pending.get(conversation_id)
        if pending and pending.expires <= self._clock():
            pending = self._pending.pop(conversation_id, None) and None
        if pending:
            if _CONFIRM.match(text):
                self._pending.pop(conversation_id, None)
                return self._execute(pending)
            self._pending.pop(conversation_id, None)  # anything else cancels it
            if _CANCEL.match(text):
                return self._out("cancelled", "Cancelled. Nothing was changed.")
        try:
            return self._dispatch(conversation_id, message_id, text)
        except ValueError as exc:
            return self._out("error", f"I could not do that: {exc}.")
        except Exception:  # never turn a task command into a failed chat turn
            return self._out("error", "I could not complete that task action. Please check the task list.")

    def _dispatch(self, cid: str, mid: str, text: str) -> dict[str, Any] | None:
        for regex, op in ((_BULK_DELETE_FINISHED, "bulk_delete_finished"), (_BULK_DELETE_ALL, "bulk_delete_all"),
                          (_BULK_COMPLETE, "bulk_complete")):
            if (m := regex.match(text)):
                return self._guard_write(cid, lambda m=m, op=op: self._bulk(cid, op, m))
        if (m := _PRIORITY.match(text)):
            return self._guard_write(cid, lambda: self._priority(m))
        if (m := _DELETE_ONE.match(text)):
            return self._guard_write(cid, lambda: self._delete_one(cid, m.group("ref")))
        if (m := _COMPLETE_TASK.match(text)):
            return self._guard_write(cid, lambda: self._complete(m.group("ref") or m.group("ref2"), True))
        if (m := _ADD_A.match(text)) or (m := _ADD_B.match(text)):
            return self._guard_write(cid, lambda: self._add(cid, mid, m.group("body")))
        if (m := _LIST_A.match(text)) or (m := _LIST_B.match(text)):
            return self._list(m.group("which"), m.groupdict().get("proj"))
        if (m := _COMPLETE_BARE.match(text)):  # no "task" word: only act on an unambiguous match
            if self._resolve(m.group("ref"), open_only=True)[0] is not None:
                return self._guard_write(cid, lambda: self._complete(m.group("ref"), False))
        return None

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _out(action: str, reply: str, **extra: Any) -> dict[str, Any]:
        return {"handled": True, "action": action, "reply": reply, "changed": 0, **extra}

    def _guard_write(self, cid: str, run: Callable[[], dict[str, Any] | None]) -> dict[str, Any] | None:
        if self.memory.is_conversation_temporary(cid):
            return self._out("refused_temporary", "This is a temporary chat, so I do not change your saved "
                             "tasks from here. Use a normal chat or the task page for that.")
        return run()

    def _tasks(self, *, status: str | None = None, project: str | None = None) -> list[dict[str, Any]]:
        rows = self.memory.personal.list(kind="task", status=status, project=project, limit=500)
        return [r for r in rows if not r["private"]]  # chat never sees private tasks

    @staticmethod
    def _short(item: dict[str, Any]) -> str:
        return item["item_id"][:8]

    @staticmethod
    def _norm(value: str) -> str:
        return " ".join(value.casefold().split())

    def _resolve(self, ref: str, *, open_only: bool) -> tuple[dict[str, Any] | None, str]:
        """Return (task, '') or (None, 'none'|'many:<ids>') for a title or id prefix."""
        ref = ref.strip().strip("\"'`").strip()
        if not ref:
            return None, "none"
        rows = self._tasks(status="open" if open_only else None)
        if _ID.match(ref.casefold()):
            by_id = [r for r in rows if r["item_id"].startswith(ref.casefold())]
            if len(by_id) == 1:
                return by_id[0], ""
        want = self._norm(ref)
        exact = [r for r in rows if self._norm(r["title"]) == want]
        hits = exact or [r for r in rows if want in self._norm(r["title"])]
        if len(hits) == 1:
            return hits[0], ""
        if not hits:
            return None, "none"
        return None, "many:" + ",".join(h["item_id"] for h in hits[:5])

    def _not_found(self, ref: str, sure: bool) -> dict[str, Any]:
        return self._out("not_found", "I could not find an open task matching that. Say \"show my tasks\" "
                         "to see them with their ids.")

    def _pick(self, ref: str, *, open_only: bool, sure: bool) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        item, why = self._resolve(ref, open_only=open_only)
        if item is not None:
            return item, None
        if why.startswith("many:"):
            ids = why.removeprefix("many:").split(",")
            rows = {r["item_id"]: r for r in self._tasks()}
            lines = [f"- {rows[i]['title']} [{i[:8]}]" for i in ids if i in rows]
            return None, self._out("ambiguous", "More than one task matches, so nothing was changed. "
                                   "Say which one by its id:\n" + "\n".join(lines))
        return None, self._not_found(ref, sure)

    def _due(self, date: str, clock: str | None) -> str:
        try:
            tz = ZoneInfo(self.memory.settings.get("owner_timezone"))
        except Exception:
            tz = ZoneInfo("UTC")
        hour, minute = (int(x) for x in (clock or "23:59").split(":"))
        try:
            local = datetime.fromisoformat(date).replace(hour=hour, minute=minute, tzinfo=tz)
        except ValueError as exc:
            raise ValueError("that date or time is not valid") from exc
        return local.astimezone(UTC).isoformat()

    # ------------------------------------------------------------------ actions

    def _add(self, cid: str, mid: str, body: str) -> dict[str, Any]:
        priority = None
        project = None
        due_at = None
        if (m := _DUE.search(body)):
            due_at = self._due(m.group("d"), m.group("t"))
            body = _DUE.sub("", body, count=1)
        if (m := _PRIO_WORD.search(body)):
            priority = _PRIORITY_VALUE[m.group("p").lower()]
            body = _PRIO_WORD.sub("", body, count=1)
        if (m := _TRAIL_PROJECT.search(body)):
            project = m.group("proj").strip()
            body = body[: m.start()]
        title = body.strip(" \t:-.,;\"'")
        if not title:
            return self._out("error", "I need a title for the task.")
        try:
            item_id = self.memory.personal.add(
                kind="task", title=title, due_at=due_at, priority=priority, project=project,
                source_conversation_id=cid, source_message_id=mid, actor="brain")
        except DuplicateItemError as exc:
            return self._out("duplicate", f"You already have an open task like that [{exc.existing_id[:8]}], "
                             "so I did not add another.")
        item = self.memory.personal.get(item_id)
        extras = []
        if due_at:
            extras.append("due " + datetime.fromisoformat(item["due_at"]).strftime("%Y-%m-%d %H:%M UTC"))
        if priority:
            extras.append(f"{_PRIORITY_NAME[priority]} priority")
        if item["project"]:
            extras.append(f"project {item['project']}")
        tail = f" ({', '.join(extras)})" if extras else ""
        return self._out("add", f"Added task \"{item['title']}\"{tail} [{item_id[:8]}].", changed=1,
                         item_ids=[item_id])

    def _list(self, which: str | None, project: str | None) -> dict[str, Any]:
        which = (which or "open").lower()
        status = {"open": "open", "overdue": "open", "finished": None, "completed": "done", "done": "done"}[which]
        rows = self._tasks(status=status, project=project.strip() if project else None)
        if which == "finished":
            rows = [r for r in rows if r["status"] in {"done", "cancelled"}]
        if which == "overdue":
            now = self._now().isoformat()
            rows = [r for r in rows if r["due_at"] and r["due_at"] < now]
        hidden = sum(1 for r in self.memory.personal.list(kind="task", status=status, limit=500) if r["private"])
        if not rows:
            return self._out("list", f"You have no {which} tasks." + self._hidden_note(hidden))
        lines = []
        for r in rows[:SHOW]:
            due = f", due {r['due_at'][:10]}" if r["due_at"] else ""
            lines.append(f"- {r['title']} [{self._short(r)}] ({_PRIORITY_NAME[r['priority']]}{due})")
        more = f"\n...and {len(rows) - SHOW} more on the task page." if len(rows) > SHOW else ""
        return self._out("list", f"Your {which} tasks ({len(rows)}):\n" + "\n".join(lines) + more
                         + self._hidden_note(hidden), item_ids=[r["item_id"] for r in rows[:SHOW]])

    @staticmethod
    def _hidden_note(hidden: int) -> str:
        return f"\n({hidden} private task(s) are not shown in chat.)" if hidden else ""

    def _complete(self, ref: str, sure: bool) -> dict[str, Any]:
        item, err = self._pick(ref, open_only=True, sure=sure)
        if err:
            return err
        outcome = self.memory.personal.complete(item["item_id"], actor="brain")
        if not outcome["changed"]:
            return self._out("complete", f"\"{item['title']}\" was already finished.")
        extra = ""
        if outcome["next_item_id"]:
            nxt = self.memory.personal.get(outcome["next_item_id"])
            extra = f" The next one is due {nxt['due_at'][:10]} [{outcome['next_item_id'][:8]}]."
        return self._out("complete", f"Marked \"{item['title']}\" done.{extra}", changed=1,
                         item_ids=[item["item_id"]])

    def _priority(self, m: re.Match[str]) -> dict[str, Any]:
        ref = m.group("ref") or m.group("ref2")
        prio = _PRIORITY_VALUE[(m.group("prio") or m.group("prio2")).lower()]
        item, err = self._pick(ref, open_only=True, sure=True)
        if err:
            return err
        changed = self.memory.personal.update(item["item_id"], priority=prio, actor="brain")
        verb = "is now" if changed else "was already"
        return self._out("priority", f"\"{item['title']}\" {verb} {_PRIORITY_NAME[prio]} priority.",
                         changed=1 if changed else 0, item_ids=[item["item_id"]])

    # ----------------------------------------------------- confirmation required

    def _request(self, cid: str, op: str, ids: list[str], description: str, sample: list[str]) -> dict[str, Any]:
        self._pending[cid] = _Pending(op, ids, self._clock() + CONFIRM_TTL_SECONDS, description)
        shown = "\n".join(f"- {t}" for t in sample[:5])
        more = f"\n...and {len(ids) - 5} more." if len(ids) > 5 else ""
        return self._out(
            "confirmation_required",
            f"{description}\n{shown}{more}\nReply \"yes\" to confirm or \"no\" to cancel. "
            "Nothing has been changed yet.", needs_confirmation=True, item_ids=list(ids))

    def _delete_one(self, cid: str, ref: str) -> dict[str, Any]:
        item, err = self._pick(ref, open_only=False, sure=True)
        if err:
            return err
        return self._request(cid, "delete", [item["item_id"]],
                             f"I will permanently delete this task [{self._short(item)}]:", [item["title"]])

    def _bulk(self, cid: str, op: str, m: re.Match[str]) -> dict[str, Any]:
        project = (m.groupdict().get("proj") or "").strip() or None
        where = f" in project {project}" if project else ""
        if op == "bulk_delete_finished":
            which = m.group("which").lower()
            statuses = {"finished": {"done", "cancelled"}, "completed": {"done"}, "done": {"done"},
                        "cancelled": {"cancelled"}, "canceled": {"cancelled"}}[which]
            rows = [r for r in self._tasks(project=project) if r["status"] in statuses]
            verb, kind, label = "delete", "delete", f"{which} tasks{where}"
        elif op == "bulk_delete_all":
            rows = self._tasks(project=project)
            verb, kind, label = "delete", "delete", f"tasks (open and finished){where}"
        else:
            rows = self._tasks(status="open", project=project)
            if m.group("which").lower() == "overdue":
                now = self._now().isoformat()
                rows = [r for r in rows if r["due_at"] and r["due_at"] < now]
            verb, kind, label = "mark done", "complete", f"{m.group('which').lower()} tasks{where}"
        if not rows:
            return self._out("nothing_to_do", f"No {label} match, so there is nothing to change.")
        if len(rows) > MAX_BULK:
            return self._out("too_many", f"That would change {len(rows)} tasks, which is more than the "
                             f"{MAX_BULK} I will do from chat. Narrow it by project or use the task page.")
        note = " Private tasks are never included." if any(
            r["private"] for r in self.memory.personal.list(kind="task", limit=500)) else ""
        return self._request(cid, kind, [r["item_id"] for r in rows],
                             f"I will {verb} {len(rows)} {label}.{note} They are:", [r["title"] for r in rows])

    def _execute(self, pending: _Pending) -> dict[str, Any]:
        done = skipped = 0
        for item_id in pending.ids:
            item = self.memory.personal.get(item_id)
            if item is None or item["private"] or (pending.op == "complete" and item["status"] != "open"):
                skipped += 1  # gone, made private, or already finished since the request
                continue
            if pending.op == "delete":
                done += 1 if self.memory.personal.delete(item_id, actor="brain") else 0
            else:
                done += 1 if self.memory.personal.complete(item_id, actor="brain")["changed"] else 0
        verb = "Deleted" if pending.op == "delete" else "Marked done"
        tail = f" {skipped} no longer matched and were left alone." if skipped else ""
        return self._out("confirmed", f"{verb} {done} task(s).{tail}", changed=done,
                         item_ids=list(pending.ids))
