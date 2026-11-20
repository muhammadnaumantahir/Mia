# Local semantic memory retrieval

Mia's existing retrieval remains the authoritative, model-independent baseline:
SQLite FTS5 candidates, provenance/trust weighting, recency, intent-aware structured
records, relationship expansion and bounded context assembly.

## Optional semantic reranking

The development branch adds an opt-in local embedding reranker:

- Enable with `MIA_MEMORY_SEMANTIC_RERANK_ENABLED=true`.
- Set `MIA_MEMORY_EMBEDDING_MODEL` to an embedding model already installed in local Ollama.
- Ollama must be reachable through a loopback HTTP URL (default
  `http://127.0.0.1:11434`). Non-loopback destinations are rejected.
- Mia calls Ollama's `/api/embed` endpoint with a bounded batch of candidate texts.
- Recent structured records, knowledge items and a bounded set of messages from
  non-temporary conversations (up to 12) are added as semantic candidates. A candidate is
  injected only when its semantic score clears the positive-match threshold.
- Existing lexical candidates are reranked too. Relationship-aware expansion still
  runs after ranking.
- If Ollama is unavailable, the model is missing, or embedding output is invalid,
  semantic candidates are discarded and lexical retrieval continues.

Document embeddings are cached by model and content hash in the existing local memory
SQLite database (bounded to 5,000 entries); only vectors are stored, not source text. The cache is cleared when source conversations or records are deleted, when messages are purged, or when a conversation is marked temporary.
No cloud embedding API, API key, vector database, or automatic model download is used.
The feature is disabled by default. It must remain local-only because retrieved memory
can contain private owner information.

## Current limitations

- This is bounded on-demand semantic reranking, not a persistent vector index.
- Conversation semantic candidates are limited to the most recent 12 messages across
  non-temporary conversations; this is not a complete persistent vector index of all
  conversation history.
- No model benchmark suite, multilingual evaluation or semantic recall/precision
  dashboard exists yet.
- Model installation and real local Ollama verification remain
  `OWNER_VERIFICATION_REQUIRED`.

## Tests

Unit tests cover cosine reranking, local endpoint restrictions, opt-in configuration,
semantic candidate recovery and the no-match fallback. They have been added but have
not yet been executed in a local checkout for this branch.
