# Mia local memory and knowledge architecture

## Separate stores for separate purposes

### Conversation memory

- Stores user and assistant turns locally in SQLite.
- The assistant sends the latest 12 turns with each new message, plus a bounded retrieved-memory block (see Contextual retrieval below) so earlier turns and other conversations are reachable.
- Conversations are identified by a random ID and can be listed, restored and deleted.
- The current web UI carries the conversation ID in its URL and displays recent conversations.
- Raw voice audio is not stored; only the text transcription is eligible for conversation history after the user sends it.

### Long-term knowledge

A knowledge item contains:
- kind: fact, note, document, experience, preference or research;
- title and text content;
- source URI/title and SHA-256 hash;
- trust level: user, trusted or untrusted;
- tags and structured metadata;
- created and updated timestamps.

SQLite FTS5 provides local full-text search. The database does not require a cloud vector service.

## Trust model

- External webpages, repositories, documents and tool outputs are saved as untrusted by default.
- The trust label is metadata, not an instruction.
- Search returns provenance so the caller can cite the source.
- Retrieved knowledge, past messages and structured decision/goal/entity memories are injected only through the bounded, delimited block described below. Competing records are detected and surfaced to the owner; Mia never silently chooses which conflicting statement is true.
- Only explicitly user-authored preferences or facts should be marked as user/trusted.

## Contextual retrieval and identity (implemented 2026-11-19)

Every chat request (`POST /api/gateway/chat` and `/api/gateway/chat/stream`) is built independently of the serving model:

1. **Canonical identity** (`mia.memory.identity`): a persisted, versioned profile (see *Persistent identity* below) rendered with the fixed honesty core and the tool/data-safety policy. It names no model and is identical whichever provider answers.
2. **Retrieval** (`mia.memory.context`): the prompt is reduced to distinctive terms (stopwords and short words dropped) and matched with SQLite FTS5 over knowledge items *and* over all past conversation messages (a `message_fts` index kept in sync by triggers, including cascade deletes and retention purges; existing databases are backfilled on startup). Candidates must match at least two distinctive terms (one, if the prompt has only one). Ranking blends normalised bm25 relevance, trust (owner-provided > trusted > untrusted), kind (preference/fact) and recency. Messages in the 12-turn recent window are excluded so nothing is repeated.
3. **Bounded block**: at most 5 knowledge items and 4 past messages, 700 characters each and 5,000 in total, deduplicated. Each entry carries a label (`[M1]`), provenance, date and source: *owner-provided*, *trusted source*, *untrusted external content*, *owner statement (not independently verified)* or *Mia's earlier reply (model-generated, unverified)*. Model output is never promoted to trusted memory.
4. **Injection resistance**: the block is a separate system message that states its contents are data, not instructions; tag-like text inside stored content that could close the block or imitate a role (`</memory_data>`, `<system>`, `<assistant>` ...) is defanged.
5. **Data-egress policy**: retrieved memory is the owner's private data. Unless `MIA_ALLOW_REMOTE_FILE_CONTEXT=true`, a request that carries retrieved memory is routed only over local (`data_local`) candidates; when no local candidate can serve it, the block is withheld (never sent to a remote provider) and a `context_withheld` Observatory event is recorded. Requests with no relevant memory are routed exactly as before.
6. **Transparency**: the chat response and the SSE `done` event include `context_references` (label, type, id, provenance, trust, title, source, timestamp, score). Observatory `context_assembled` events store reference ids only, never content, plus the identity version.

`MIA_CONTEXT_RETRIEVAL=false` disables retrieval. Retrieval failures never block a reply (`context_retrieval_failure` event).

Known limits: lexical (FTS5) matching remains the default, so paraphrases with no shared words may be missed; no embedding provider ships with Mia; contextual-reference matching uses conservative English patterns; old conversation summarisation is not implemented. Relationship edges are followed for structured records, and the Memory page displays identity, provenance, capture suggestions, upkeep and relationship controls.

## Persistent identity (implemented 2026-11-19)

Mia's identity is stored data in the memory database (`identity_versions`), not source code and not rebuilt from conversations.

- **Profile fields:** `name`, `purpose`, `personality[]`, `principles[]`, `communication_style{tone, verbosity, language}`, `owner_relationship`, `interaction_preferences[]`. The factory default (version 1, created on first start) contains no owner details and renders to exactly the original prompt text.
- **Fixed core:** the honesty/AI-status rules and the tool and data-safety policy are always included and cannot be edited through the profile.
- **Versioning:** every change appends an immutable version with `source`, `reason`, `parent_version` and a SHA-256 of the profile. A request that changes nothing creates no version. Rollback restores an earlier profile as a *new* version; history is never rewritten. Updates run in a `BEGIN IMMEDIATE` transaction, so concurrent updates serialise.
- **Control:** only the owner API (`source` of `owner`, `system` or `import`) can change identity; nothing in the chat or capture path can. Text fields reject credential-shaped content (`mia.memory.safety`), unknown fields and over-long values.
- **Model independence:** both chat endpoints read the stored identity on every request, so an edit applies to the next reply and the same prompt is sent whichever provider or model answers. If the identity cannot be read, chat continues with the factory default and an `identity_load_failure` Observatory event is recorded.
- **Audit:** `identity_created`, `identity_updated` and `identity_rolled_back` entries are written to `memory_audit_log` in the same transaction as the change, holding version numbers and changed field names, never the profile text.

Endpoints: `GET /api/identity`, `GET /api/identity/history`, `PUT /api/identity` (partial update plus `reason`), `POST /api/identity/rollback`, `GET /api/memory/audit`.

## Schema versioning (implemented 2026-11-19)

`schema_migrations` records applied versions. Version 1 is the original layout; version 2 adds `identity_versions` and `memory_audit_log`. Migrations are additive, run once inside one transaction and never rewrite memory rows. A database whose schema is newer than the running build is refused rather than opened.

Verified with local automated tests only (mocked providers, no live model): restart persistence, provider/model switch with identical prompts, migration of a schema-1 database with existing items, messages and records, concurrent updates, and failure fallback.

## Memory lifecycle and capture (implemented 2026-11-20)

**Record types:** decision, goal, episode, project, entity, fact, preference, lesson.
**Statuses:** pending (suggestion, never retrieved), active, completed, outdated, archived, expired, superseded, rejected. Only active and completed records are in the search index and retrievable; leaving those states removes the index row in the same transaction.
**Trust labels (`provenance`):** `owner_provided` (stated or confirmed by the owner), `observed` (automatically captured, NOT confirmed), `inferred`, `hypothesis`, `source_backed`, `validated`. Approving or confirming a suggestion makes it `owner_provided` and keeps the earlier label in `origin`.
**Other fields:** importance, confidence, temporary + `expires_at`, `origin` (conversation id, message id, capture mode), edit history snapshots, per-reply usage log, typed relationships with `private` visibility (private links never reach a prompt). Decisions cannot be edited in place, only corrected (a successor is created and the original kept).

**Capture pipeline** (`mia.memory.capture`, runs on the owner's own chat messages only, never on assistant output, and never breaks chat if it fails):

1. Memory off, or a temporary conversation: nothing is captured.
2. Explicit commands, honoured while memory is on: "Remember/Don't forget/Keep in mind ...", "My preferred X is ...", "Update what you remember about X to Y", "Forget ...". "Remember this" refers to the owner's previous message; if there is none Mia asks.
3. Automatic capture (`capture_mode`: `off`, `approval` (default), `auto`): pattern-based and conservative. Skipped: questions, hedged or hypothetical statements, "right now/for now" context, sensitive personal attributes (religion, health, politics, sexuality, ethnicity, immigration, finances, etc.), and credentials.
4. Duplicates are skipped (token similarity >= 0.8, and the same message id is never processed twice). Previously rejected suggestions are not re-suggested.
5. Conflicts (same subject or similarity >= 0.5): automatic capture never overrides an existing memory; the suggestion is queued with the conflicting ids. An explicit statement about the same subject supersedes the old record (history kept); a merely similar one is saved and the possible conflict is reported.
6. In `approval` mode everything automatic is a pending suggestion. In `auto` mode only candidates at or above `auto_save_min_confidence` with no conflicts are saved active (still labelled `observed`).

**Forgetting:** "Forget X" (or `DELETE /api/memory/records/{id}`) deletes the record, its whole correction chain, edit history, relationships, usage rows and index rows, **and by default the chat message(s) it was captured from** (`purge_source=false` keeps them), so the fact cannot resurface through conversation search. If more than one memory matches, nothing is deleted and Mia asks which. The "Forget ..." command message itself stays in history and names only the topic. Audit entries hold ids and counts, never memory text.

**Known limits:** capture is heuristic English pattern matching, not a model; it misses things by design. Contradictions phrased with unrelated wording are not detected. There is no tombstone, so an automatic suggestion about a forgotten (not rejected) topic can be proposed again. Knowledge items (`memory_items`) still have trust levels only, not lifecycle states.

Endpoints: `POST /api/memory/capture`, `POST /api/memory/records/{id}/(edit|approve|reject|confirm|archive|outdated|restore|temporary)`, `GET/PUT /api/memory/settings`, `POST/DELETE /api/memory/relationships`, `POST /api/conversations/{id}/temporary`, `GET /api/memory/replies/{id}/usage`, `GET /api/conversations/{id}/derived-memory`, `DELETE /api/conversations/{id}[?forget_derived=true]` (keeps memories captured from the conversation unless opted in; always lists them). Chat responses include `memory_capture`.

## Retrieval (upgraded 2026-11-20)

`assemble_memory_context` (model-independent; the same block is built whichever provider answers) now:

- **Honours the owner's switches:** `memory_enabled` or `retrieval_enabled` off means no memory is retrieved.
- **Ranks** records by full-text relevance (SQLite FTS5 bm25), provenance trust, record type, recency, **importance** and **confidence** (both centred on 0.5, so a default record is neutral), with a small boost for memories captured in the current conversation. Pending, outdated, archived, expired, superseded and rejected records are never retrieved.
- **Understands contextual references** by pulling records by type when the request names none of their words: "Continue my previous project" (project/goal/decision), "What did we decide last time?" (decision), "Use my usual preferences" (preference), "What was the problem we encountered earlier?" (lesson/episode/project), "What is the current state of this feature?" (project/goal/decision), "What are my goals?" (goal). Pattern-based, English only.
- **Follows relationships:** up to 4 records linked (non-private, retrievable) to the top three results are added and labelled `linked (relation)`. Private links and archived/pending endpoints are never followed.
- **Stays bounded:** at most 4 records (6 for a contextual reference), 5 knowledge items, 4 past messages, about 5,000 characters; duplicates collapse. A 1,500-record store retrieves in well under a second in tests; the database is never loaded wholesale.
- **Optional semantic hook:** `assemble_memory_context(..., reranker=fn)` accepts a function that returns score boosts for candidate records. **No embedding provider ships with Mia**, so retrieval is lexical today; if a reranker raises, retrieval silently continues lexically.
- **Records usage:** each reply logs the memories placed in its prompt (`memory_usage`), bumps `use_count`/`last_used_at`, and returns a `reply_id`; `GET /api/memory/replies/{reply_id}/usage` and the record detail (`used_in_replies`) expose it. Usage rows are deleted with the memory, the knowledge item or the conversation.

Retrieved memory still reaches only local models unless `MIA_ALLOW_REMOTE_FILE_CONTEXT=true`.

## Maintenance and conflict handling (implemented 2026-11-20)

`POST /api/memory/maintenance` (`{"apply": false, "stale_days": 180}`) reports; with `apply: true` it also runs the only two automatic sweeps. Nothing else is ever changed without the owner.

- **Automatic (apply only):** temporary memories past `expires_at` become `expired`; suggestions unreviewed for `pending_retention_days` (default 30) become `expired`. A sweep for expired temporary memories also runs at application start, and expired memories are excluded from retrieval even before a sweep runs.
- **Reported, never auto-resolved:**
  - *Duplicate clusters* (same type, token similarity >= 0.8) with a suggested keeper (owner-provided first, then importance, then oldest). `POST /api/memory/records/consolidate` merges on the owner's approval: the copy becomes `superseded` and leaves retrieval and the search index but its text is kept; deleting the kept record later removes the copies too.
  - *Conflicts*: active records of one type with the same subject or similar wording but different content (`same_subject` / `similar_wording`). Mia does not choose which is true; chat context marks competing records so the model asks the owner, and the owner resolves them by correcting, archiving or deleting.
  - *Possibly stale* goals and projects (not updated or used for `stale_days`). Decisions, preferences and facts are never flagged by age alone.
- **Confidence updates:** repeating an automatic suggestion raises its confidence by 0.1 (cap 0.95) without changing its trust label; an owner-provided memory's confidence never changes; stating a suggestion explicitly confirms it (`owner_provided`). Retrieval also gives a small bonus to memories that have proven useful (`use_count`).
- **History:** corrections create successors, edits snapshot the previous version, merges keep the text, and decisions can only be corrected, never edited in place. A maintenance run was tested not to alter a decision or its history.
- Duplicate detection at capture time matches across record types (identical text is one memory); conflict detection stays within a type.

Limits: similarity is token overlap, so paraphrases and contradictions with unrelated wording are not detected; maintenance analyses at most 400 active records per type per run (the report says when it truncated); there is no scheduler, so maintenance runs at start-up (expiry only) or when the owner triggers it.

## Structured memory records (implemented 2026-11-19)

`mia.memory.structured` adds records with a lifecycle, stored in the same SQLite database (`structured_memory` plus an FTS5 index), for things that change or get corrected: **decision, goal, episode, project, entity**.

- **Provenance:** `owner_provided` (default for the owner API), `observed`, `source_backed`, `inferred`, `hypothesis`, `validated`. `inferred`, `validated` and `source_backed` records must cite at least one evidence reference (`message`, `knowledge`, `record`, `url`, `tool_event`, `other`); a hypothesis may have none. Confidence is optional and must be a finite number in 0..1.
- **Corrections never overwrite.** `POST /api/memory/records/{id}/correct` creates a successor (linked to the original as evidence) and marks the original `superseded` in one transaction; the original text stays in history. Only active/completed records can be corrected, and a derived-provenance correction must supply its own evidence (the automatic link to the original does not count).
- **Status:** `active`, `completed`, `superseded`, `rejected`. Owners can move a record between active and completed or reject it (final). Superseded and rejected records are removed from the search index and never injected into prompts; they remain in history/export.
- **Competing claims** are surfaced, not merged: creating or correcting into an active record with the same normalised title and type returns `conflicts_with`, and retrieved records that still have an active rival carry a `COMPETING` marker telling the model to ask the owner which is current.
- **Context assembly:** records are a third candidate pool (max 4) ranked by relevance, provenance weight (owner-provided/validated highest, hypothesis lowest), record type (decision/goal/project favoured) and recency. The block shows type, status, provenance label, date, optional confidence, and a decision's stored `rationale`. Hypotheses and inferences are labelled unverified. All text is defanged like other retrieved content, and the same local-only egress rule applies.
- **Export** (`GET /api/memory/export`) now includes `structured_memory` inside the checksummed payload.
- **Audit:** `record_created`, `record_corrected`, `record_status_changed` and `record_deleted` Observatory events carry ids and metadata only, never record text.

Endpoints: `POST/GET /api/memory/records`, `GET /api/memory/records/{id}` (with correction history), `POST /api/memory/records/{id}/correct`, `POST /api/memory/records/{id}/status`, `DELETE /api/memory/records/{id}`.

Known limits: records are created only through the owner API (nothing extracts decisions or goals from conversations automatically, and there is no web UI for them yet); the retention purge does not touch structured records (owner deletion does); matching is lexical; there are no entity relationships or automatic consolidation; a stale earlier assistant reply can still be retrieved next to a corrected record, labelled as unverified model output.

## API

- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `DELETE /api/conversations/{conversation_id}`
- `GET /api/memory/items`
- `GET /api/memory/export` — bounded (25 MB) JSON export of conversations, messages and knowledge items with a SHA-256 integrity digest
- `GET /api/memory/search?q=...`
- `POST /api/memory/items`
- `DELETE /api/memory/items/{item_id}`

## Retention and privacy

- The database is local under `MIA_DATA_DIR`.
- Chat messages are persisted so the conversation can continue after a restart.
- User can delete an individual conversation or knowledge item.
- No conversation history is sent to a different provider except the current selected provider, as part of the conversation prompt.
- Do not store passwords, API keys, session cookies, recovery phrases or other secrets as memory.
- Back up the database only to a location controlled by the user.
- The export endpoint returns the full local conversation and knowledge contents; keep exported files private. Exports above 25 MB are rejected rather than silently truncated. The digest covers the canonical export payload excluding the digest field itself.


## Office document parser boundary

DOCX, XLSX and PPTX are ZIP-based containers. Before an optional parser is loaded, Mia inspects the archive directory and rejects encrypted entries, malformed ZIP containers, excessive entry counts, individual members above 64 MB, total expanded content above 128 MB, and compression ratios above 200:1 for members larger than 1 MB. These limits reduce archive-expansion risk but do not replace parser sandboxing or target-device security testing. Imported document content remains untrusted regardless of parser validation.

## Retention and bulk deletion

`POST /api/memory/purge` lets the owner remove conversations and/or knowledge items whose last update is older than `older_than_days` (minimum 1). It never runs automatically. The default is `dry_run: true`, which returns counts and up to 20 sample titles per kind without deleting anything; deletion requires an explicit `dry_run: false`. A real purge runs in one transaction (messages, items and their full-text index rows) and records a `retention_purge` Observatory event with counts only. There is no automatic expiry, and the Observatory event log and usage ledger are not touched. The assistant sidebar's Memory cleanup panel previews first and only offers deletion for the previewed age, behind a browser confirmation.


## Record-level project scoping (MIA-BRAIN-06)

Design note. Structured records carry an optional `project` (nullable column added by
migration 5; `NULL` = unscoped, the default, so existing records and behaviour are unchanged).

- **Opt-in.** A record is scoped only when the owner sets a project (API `project` field on
  create, `POST /api/memory/records/{id}/project`, audited) or when it is captured in a
  conversation the owner has already assigned to a project.
- **Recall.** A conversation with no project recalls any record (original behaviour). A
  conversation in a project recalls that project's records and unscoped records, never
  another project's. The rule (`mia.memory.context.record_in_scope`) is applied to keyword,
  intent, semantic and linked-record candidates, so a relationship link cannot carry an
  out-of-scope record into a prompt.
- **Capture.** Duplicate/conflict checks and explicit "update X" only consider in-scope
  records, so a statement in one project never confirms or supersedes another project's
  record. "Forget X" stays unscoped on purpose: owner deletion is never blocked by scope.
  Temporary conversations are still never captured from. Corrections keep the project.
- **Limits.** Scope is a recall filter applied after retrieval, so a pool dominated by
  another project's records can return fewer candidates. Project names match
  case-insensitively and are not a permission boundary or a security control. No UI yet.

## Personal data model and privacy contract (MIA-LIFE-01)

Owner-scoped tasks, reminders and notes live in `personal_items` (migration 6) in the same
SQLite file as the rest of memory. Code: `src/mia/memory/personal.py`; API: `/api/personal/items`.

- **Scope.** Single local owner. Kinds: `task`, `reminder`, `note`; status `open`/`done`/`cancelled`.
  A reminder requires a timezone-aware `due_at` (stored as UTC); recurrence, timezone display
  and duplicate handling belong to MIA-LIFE-02.
- **Provenance.** `owner_provided`, `imported`, or `conversation_captured` (must cite the source
  conversation and message). Nothing creates items automatically yet.
- **Not in model context.** Items are not indexed for retrieval and are never injected into
  prompts or sent to a provider. A future task must opt in explicitly; `private` items must
  never be included. Tests assert items do not appear in assembled memory context.
- **Project.** Optional, same normalised name as conversations and records; an owner-view
  filter, not a security boundary.
- **Secrets.** Credential-like title/details are refused.
- **Delete / retention.** Delete is a hard delete; the audit log keeps ids, kind and field
  names only. Retention is owner-triggered: `POST /api/personal/items/retention` previews by
  default and only ever removes done/cancelled items older than the period passed.
- **Export.** `GET /api/memory/export` now includes `personal_items`, unfiltered.
- **Encryption boundary.** Not encrypted at rest here (plain SQLite, like other memory); encrypted
  backup/restore is MIA-LIFE-11. No remote/device sync (MIA-LIFE-13).

## Task manager semantics (MIA-LIFE-02)

Built on `personal_items` (migration 7 adds `priority`, `recurrence`, `series_id`, a duplicate
key and the `personal_item_history` table). API: `/api/personal/items` (+ `/complete`, `/history`).

- **Priority.** 1 high, 2 normal (default), 3 low; list filter `priority`.
- **Time.** All timestamps are stored in UTC and must be timezone-aware on input. The owner's
  wall-clock zone is the memory setting `owner_timezone` (IANA name, default `UTC`).
  Filters: `overdue` (open and past due) and `due_before`.
- **Recurrence.** `{"freq": "daily|weekly|monthly", "interval": 1..365}`, requires a due time.
  Completing a recurring item with `complete` marks it done and creates the next occurrence:
  the first one after both the old due time and now, computed in the owner's timezone, so a
  09:00 task stays 09:00 local across daylight-saving changes and an overdue daily task does
  not pile up missed copies. Monthly rules clamp to the month's last day without drifting
  (31st -> 28th -> 31st). A local time that does not exist on a day (DST gap) resolves to the
  earlier offset. Completing twice never creates a second occurrence. Changing status to done
  on a recurring item through `PATCH` is refused; use `complete`.
- **Duplicates.** An open item with the same kind, normalised title and project is refused
  (HTTP 409 with `existing_item_id`) on create, rename, project change and reopen;
  `allow_duplicate` overrides. Finished items never block. Rows created before migration 7
  have no duplicate key, so they do not block either.
- **History.** Status, priority, due time, project, recurrence and privacy changes keep old and
  new values; title and details changes record only that the field changed. History is deleted
  with the item, included in the export (`personal_item_history`), and never holds item text.
- **Limits.** No UI, no Brain intents (LIFE-03), no reminders delivery (LIFE-09), no calendar
  integration, no sub-tasks. Recurrence is "after completion", not a fixed calendar schedule.

## Project workspace (MIA-LIFE-04, data/API half)

`projects` + `project_milestones` (migration 8); code `src/mia/memory/projects.py`; API
`/api/projects` (+ `/{name}`, `/{name}/milestones`). The registry only adds a goal, a status
(`active|paused|completed|archived`) and milestones (open/done, optional timezone-aware due
time). Tasks (`personal_items`), decisions (structured records of type `decision`, scoped by
MIA-BRAIN-06) and conversations (assigned by the owner) stay in their own stores and are
*selected* by project key, never copied.

- **Isolation.** A workspace shows only items whose project key matches (case/spacing-
  insensitive). Unassigned material and other projects' tasks, decisions, conversations and
  milestone ids never appear; a milestone id from another project is refused. Temporary
  (private) conversations are never listed or counted. Finished tasks and retired decisions
  are hidden. The name is an owner-view filter, not a security boundary.
- **Unregistered projects.** A name that appears on tasks, decisions or chats but has no
  registry row still gets a workspace view (`registered: false`) and appears in the listing.
- **Delete.** Removes goal, status and milestones only; labelled work keeps its label.
- **Audit/export.** Audit holds ids and field names only (no goal or titles); export includes
  `projects` and `project_milestones`.
- **Not done (honest limits).** No UI (the A6 half of this task is untouched), no rename of a
  project (it would have to rewrite every store), names containing `/` cannot be addressed in
  the URL path, and the Brain does not yet receive the goal/milestones in its prompt context;
  project-scoped *recall* of conversations and records already exists (M1 project continuity,
  MIA-BRAIN-06).

## Daily and weekly briefing (MIA-LIFE-05)

`GET /api/briefing` (code: `src/mia/memory/briefing.py`). Deterministic and model-free: it is
assembled from stored records, so every line traces to a record. Nothing is sent from here.

- **Sources.** Open tasks and reminders, open dated project milestones (active projects only) and
  the goals of active projects. **There is no calendar source**: no calendar event is ever listed,
  and `missing_sources` always says so. Empty sources are reported as empty, not filled in.
- **Citations.** Each item carries `source` (`personal_item` / `project_milestone` / `project` and
  its id); the text form shows the type and short id. The briefing also reports `timezone` (the
  `owner_timezone` setting), `generated_at`, a freshness block per source, undated open items and
  items omitted by the per-section limit.
- **Windows.** `daily` = the rest of today in the owner's local day, `weekly` = through the 7th
  local day; `horizon_days` (1-31) overrides. Overdue = open and due before now. An item due
  exactly now is "coming up".
- **Privacy.** `private` items are hidden and only counted unless `include_private=true`.
  `project=` limits everything to one project (no other project's items appear).
- **Goal follow-up.** An active project with a goal and no task, milestone or goal change for
  `stale_goal_days` (default 14) is listed with the days idle and its last activity.
- **Quiet hours.** Optional `quiet_start_minute`/`quiet_end_minute` (minutes after local midnight;
  overnight windows work). Content is still built; `deliver_now` is false inside the window.
  No quiet hours are assumed when none are given. Delivery itself belongs to MIA-LIFE-09.
- **Not done.** No scheduler or notification, no persisted briefing preferences (configuration is
  per request), no UI, no model-written summary, no Brain intent, no calendar integration.

## Proactive stalled-work suggestions (MIA-LIFE-14)

`POST /api/life/suggestions/run` (code: `src/mia/life/suggestions.py`). Deterministic and
model-free; the message is a template over stored fields. A **dry run is the default** and sends
nothing. Sent suggestions go to the in-app outreach inbox only (pull, never push).

- **What Mia may speak up about.** An *idle goal* (active project with a goal and no task,
  milestone or goal change for `stale_goal_days`) and an *idle task* (open, non-private, no change
  for `suggestion_idle_task_days`, not already due/overdue). Due and overdue items belong to the
  notification center (MIA-LIFE-09), so the two never duplicate. Private tasks never produce a
  suggestion.
- **Suppression, in order, with the reason reported.** `stale` (idle > 90 days: too old to
  interrupt about), `low_confidence` (Mia captured it from a chat and the owner never touched it
  for 14 days; confidence 0.4 < 0.5), `low_relevance` (below `suggestion_min_relevance_pct`,
  default 40), `duplicate` (same title and project as a higher-ranked candidate), then the shared
  `OutreachEngine` policy: emergency stop, outreach opt-in (`outreach_enabled`, off by default),
  paused, quiet hours, daily cap, minimum gap, unanswered backlog, dismissed-topic memory (30 days)
  and per-topic cooldown. At most `max_per_run` (1-3, default 1) are sent per run.
- **Relevance** is a documented formula, not a model score: task 0.45, goal 0.55, priority 1 `+0.2`,
  priority 3 `-0.2`, plus up to `+0.2` as idle time grows beyond the threshold.
- **Evidence and rationale.** Each message cites its record (`personal_item` or `project`) and says
  how long the item has been idle.
- **Withdrawal and opt-out.** Pending suggestions whose evidence changed (task done, deleted, edited,
  made private; goal edited, paused or active again) are withdrawn (`withdrawn` state, which frees the
  unanswered backlog but keeps the cooldown). Turning `suggestions_enabled` off or pressing the
  emergency stop withdraws all pending suggestions on the next run. Other outreach kinds are never touched.
- **Not done.** No scheduler: it runs when called (owner request), and the autonomous runner does
  not call it yet. Suppressed candidates are returned in the run report but not stored.
  No notification-center delivery, UI, calendar, model-written text or live evidence.

## Project frame in the prompt (MIA-LIFE-04)

When a conversation belongs to a registered project workspace, the first item of the retrieved-memory block (`[M1]`,
reference type `project`) is the owner's own frame for that project: its status, goal, up to five open milestones (due date,
flagged `overdue` when past) and the number completed, in at most about 900 characters. Only the conversation's own project
is ever read, so another project's goal cannot reach the prompt. The block is built from owner-authored text, neutralised and
bounded like every other memory item, and it is memory: the Brain's privacy routing keeps a prompt that contains it on local
models unless the owner allowed remote context, and the owner's `memory_enabled` / `retrieval_enabled` switches turn it off.
It appears on every non-trivial turn (greetings and acknowledgements are still skipped by the Brain), even when no stored
record matches the question, and later retrieved items are labelled `[M2]`, `[M3]` and so on. Nothing is added for a
conversation without a project, for a project label with no registered workspace, or after the workspace is deleted.

