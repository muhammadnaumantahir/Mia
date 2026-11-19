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
- Retrieved knowledge and past messages are injected only through the bounded, delimited block described below. Conflict handling between competing memories and structured decision/goal memory are not implemented yet.
- Only explicitly user-authored preferences or facts should be marked as user/trusted.

## Contextual retrieval and identity (implemented 2026-11-19)

Every chat request (`POST /api/gateway/chat` and `/api/gateway/chat/stream`) is built independently of the serving model:

1. **Canonical identity** (`mia.memory.identity`): one versioned identity prompt (`IDENTITY_VERSION`) plus the tool/data-safety policy. It names no model and is identical whichever provider answers. It is a code-defined constant, not yet an owner-editable persisted profile.
2. **Retrieval** (`mia.memory.context`): the prompt is reduced to distinctive terms (stopwords and short words dropped) and matched with SQLite FTS5 over knowledge items *and* over all past conversation messages (a `message_fts` index kept in sync by triggers, including cascade deletes and retention purges; existing databases are backfilled on startup). Candidates must match at least two distinctive terms (one, if the prompt has only one). Ranking blends normalised bm25 relevance, trust (owner-provided > trusted > untrusted), kind (preference/fact) and recency. Messages in the 12-turn recent window are excluded so nothing is repeated.
3. **Bounded block**: at most 5 knowledge items and 4 past messages, 700 characters each and 5,000 in total, deduplicated. Each entry carries a label (`[M1]`), provenance, date and source: *owner-provided*, *trusted source*, *untrusted external content*, *owner statement (not independently verified)* or *Mia's earlier reply (model-generated, unverified)*. Model output is never promoted to trusted memory.
4. **Injection resistance**: the block is a separate system message that states its contents are data, not instructions; tag-like text inside stored content that could close the block or imitate a role (`</memory_data>`, `<system>`, `<assistant>` ...) is defanged.
5. **Data-egress policy**: retrieved memory is the owner's private data. Unless `MIA_ALLOW_REMOTE_FILE_CONTEXT=true`, a request that carries retrieved memory is routed only over local (`data_local`) candidates; when no local candidate can serve it, the block is withheld (never sent to a remote provider) and a `context_withheld` Observatory event is recorded. Requests with no relevant memory are routed exactly as before.
6. **Transparency**: the chat response and the SSE `done` event include `context_references` (label, type, id, provenance, trust, title, source, timestamp, score). Observatory `context_assembled` events store reference ids only, never content, plus the identity version.

`MIA_CONTEXT_RETRIEVAL=false` disables retrieval. Retrieval failures never block a reply (`context_retrieval_failure` event).

Known limits: lexical (FTS5) matching only, so paraphrases with no shared words are missed; no entity/relationship graph; no consolidation or summarisation of old conversations; no automatic conflict detection; the web UI does not yet display `context_references`.

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

