# Local semantic memory retrieval

Mia's model-independent retrieval baseline remains authoritative: SQLite FTS5
candidates, provenance/trust weighting, recency, intent-aware structured records,
relationship expansion and bounded context assembly.

## Optional semantic reranking

The optional local embedding reranker is disabled by default:

- Enable with `MIA_MEMORY_SEMANTIC_RERANK_ENABLED=true`.
- Set `MIA_MEMORY_EMBEDDING_MODEL` to an embedding model already installed in local Ollama.
- Ollama must be reachable through a loopback HTTP URL (default
  `http://127.0.0.1:11434`). Non-loopback destinations are rejected.
- Mia calls Ollama's `/api/embed` endpoint with bounded batches.
- The candidate pool includes recent structured records and knowledge items, plus
  messages across the complete retained history of non-temporary conversations.
  Recent turns from the active conversation are excluded from long-term recall because
  they are already supplied as ordinary conversation context.
- Embeddings are cached by model ID and content SHA-256 in Mia's existing SQLite memory
  database. Cache reads/writes are batched, and uncached documents are embedded in
  batches of at most 64.
- Existing lexical candidates are reranked too. Semantic-only candidates are included
  only when the local embedding score clears the positive-match threshold.
- If Ollama is unavailable, the model is missing, or embedding output is invalid,
  semantic candidates are discarded and lexical retrieval continues.

The first query after enabling/upgrading may require embedding a substantial portion of
uncached retained conversation history. Subsequent queries reuse cached vectors.
Embedding requests are local-only; no cloud embedding API, API key, vector database,
or automatic model download is used. Do not enable this feature on a machine without
sufficient local resources.

## Relationship expansion

After lexical/semantic ranking, structured-memory recall follows normal-visibility
relationships from up to three of the strongest retrieved structured records. Traversal
is limited to two hops and at most four additional records. Each hop receives a relevance
penalty. Private relationships and records outside active/completed status are excluded.
Relationship expansion is an enhancement: failures must not prevent ordinary memory
retrieval or a chat response.

## Current limitations

- This is bounded on-demand semantic reranking, not a continuously maintained vector
  database or a persistent ANN index.
- Full-history coverage means all retained, non-temporary conversation messages are
  considered as candidates; it does not mean every message is injected into the prompt.
  The final context remains limited by the configured candidate, record, message and
  character budgets.
- The first uncached full-history query can be slower and consume local CPU/RAM.
- No measured multilingual semantic recall/precision benchmark or quality dashboard is
  available yet.
- Local Ollama model installation, latency and retrieval quality still require
  `OWNER_VERIFICATION_REQUIRED`.

## Tests and verification

Regression tests cover old messages outside the recent window, temporary-conversation
exclusion, embedding-cache round trips, relationship traversal depth, private-link
exclusion and bounded relationship expansion. Tests have been authored; do not treat
them as passing until they are run in a local checkout.
