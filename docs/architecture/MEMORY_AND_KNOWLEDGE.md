# Mia local memory and knowledge architecture

## Separate stores for separate purposes

### Conversation memory

- Stores user and assistant turns locally in SQLite.
- The assistant sends the latest 12 turns with each new message.
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
- The current gateway does not automatically inject arbitrary retrieved knowledge into a model prompt. Retrieval augmentation must later add source quoting, conflict handling, prompt-injection resistance and policy enforcement.
- Only explicitly user-authored preferences or facts should be marked as user/trusted.

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

