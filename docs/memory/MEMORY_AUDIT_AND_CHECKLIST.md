# Persistent identity and long-term memory — audit and checklist

**Audit date:** 2026-11-19
**Audited commit:** `bd499f40df2d057d2a360a23ede9ae8bc10913ef` (`main`, voice work present)
**Baseline verification (local, Linux, Python 3.13.18, no CI):** `pytest` 422 passed, 1 skipped;
`ruff check src tests` reported 1 pre-existing import-order finding in
`tests/test_voice_ui_contract.py` (fixed in the same commit as this document).

This records what the code does today, based on reading it. Nothing here is a claim about
behaviour I have not read; items marked *not verified* were not exercised.

## What actually exists and works (read in source, covered by existing tests)

| Area | Evidence |
|---|---|
| Conversation persistence in SQLite (`conversations`, `conversation_messages`) | `memory/store.py` |
| Knowledge items (`memory_items`: fact, note, document, experience, preference, research) with trust level, tags, source URI/hash, FTS5 index | `memory/store.py` |
| Message FTS index (`message_fts`) with triggers and startup backfill | `memory/store.py` |
| Structured records (decision, goal, episode, project, entity) with status, provenance, confidence, evidence, supersede-on-correct, same-title conflict surfacing | `memory/structured.py` |
| Lexical context assembly: FTS5 over items, messages and records, ranking by relevance/trust/kind/recency, bounded block, dedupe, tag defanging | `memory/context.py` |
| Local-only egress rule for retrieved memory unless `MIA_ALLOW_REMOTE_FILE_CONTEXT=true` | `observatory/api.py::prepare_chat_context` |
| Retrieval failure never blocks chat | same |
| Context references returned with chat replies; UI shows source chips | `api.py`, `assistant.html` |
| Checksummed JSON export (25 MB cap) including structured records | `store.export_data` |
| Owner-triggered retention purge (dry-run default) | `store.purge_older_than` |
| Item and record deletion remove FTS rows in the same transaction | `store.delete_item`, `structured.delete` |

## Gaps against the Feature 2 specification

| # | Requirement | Current state |
|---|---|---|
| 1 | Persistent, versioned, owner-editable identity | Identity is a **code constant** (`IDENTITY_VERSION`, `IDENTITY_PROMPT`). Not persisted, not editable, no history, no owner relationship, no communication-style settings. |
| 2 | Semantic vs episodic vs project/goal/decision/relationship memory | Structured types exist, but no relationship memory and no lessons-learned type. Knowledge items have no lifecycle/confidence/status. |
| 3 | Intelligent capture pipeline | **Absent.** Nothing extracts memory from chat; no "remember this"/"forget this" handling; no pending-suggestion queue. |
| 4 | Consent and capture settings (auto / approval / off / temporary conversation) | **Absent.** No settings store. |
| 5 | Secret rejection | **Absent** for memory writes. |
| 6 | Confirmed vs inferred vs temporary vs unverified vs outdated | Partly: provenance on structured records only. Knowledge items use trust level only. |
| 7 | Retrieval | Lexical only. No project/conversation filter, no confidence weighting for items, no semantic fallback layer, no persisted record of what was used per reply. |
| 8 | Maintenance | No duplicate detection for items, no consolidation, no archive state, no expiry for temporary memories, no edit-in-place with history for items. |
| 9 | Management UI | Read-only structured-record list plus chips. No search, filter, edit, archive, delete, correct, approve/reject, or settings controls. |
| 10 | Portability | Export only. **No import, no backup/restore, no schema version table or migrations.** |
| 11 | Security | **No authentication anywhere on the API** (protection is localhost binding in `__main__.py` only). No owner-token check, no Host/Origin check, no durable memory audit log (only Observatory events). |
| 12 | Provider-switch and restart tests | Not present as dedicated tests. |

## Design decisions

- Keep SQLite/FTS5 and the existing tables. No new engine.
- Add a schema-version table and additive migrations; never drop or rewrite existing memories.
- Persisted identity lives in the database; the code constant remains only as the factory default.
- Semantic retrieval is optional behind an interface; the FTS5 path stays the working fallback.
- Authorization: opt-in owner token (`MIA_OWNER_TOKEN`) plus Host/Origin checks on all memory routes, so the
  existing no-config localhost workflow keeps working. Behaviour with and without the token is tested.
- Owner details are never hardcoded; the identity profile is data.

## Implementation checklist (status updated as milestones are pushed)

- [x] M1 Audit and checklist
- [x] M2 Persistent versioned identity (storage, API, history, prompt integration, restart/provider-switch tests) — local `pytest` 450 passed, 1 skipped; `ruff` clean
- [x] M3 Memory data architecture: record lifecycle (pending/active/completed/outdated/archived/expired/superseded/rejected), importance, temporary+expiry, edit history, relationships with privacy, owner settings, per-reply usage log, temporary conversations, schema v3 — local `pytest` 484 passed, 1 skipped; `ruff` clean. Not done: lifecycle states for knowledge items (`memory_items`), which remain trust-level only
- [ ] M4 Capture pipeline: explicit commands, settings/consent, pending queue, secret filter, provenance
- [ ] M5 Retrieval upgrade: lifecycle-aware filtering, confidence weighting, per-reply usage log, optional semantic hook
- [ ] M6 Maintenance: duplicates, contradictions, archive, expiry, update history
- [ ] M7 Management UI and settings controls
- [ ] M8 Security (owner auth), export/import/backup/restore
- [ ] M9 Full regression run, documentation, final report

Acceptance criteria are tracked in the final report; none are claimed complete by this audit.
