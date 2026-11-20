import pytest

from mia.memory.semantic import LocalOllamaEmbeddingReranker


def test_local_embedding_reranker_prefers_semantically_closer_records():
    def fake_embed(texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([1.0, 0.0] if "query" in lowered or "billing" in lowered else [0.0, 1.0])
        return vectors

    reranker = LocalOllamaEmbeddingReranker(
        model="test-embedding",
        embedder=fake_embed,
    )
    boosts = reranker(
        "query",
        [
            {"record_id": "billing", "title": "Billing decision", "body": "PostgreSQL"},
            {"record_id": "garden", "title": "Garden note", "body": "Water tomatoes"},
        ],
    )
    assert boosts["billing"] > boosts["garden"]
    assert 0.0 <= boosts["billing"] <= 0.3


@pytest.mark.parametrize("url", [
    "https://example.com",
    "http://192.168.1.20:11434",
    "http://metadata.google.internal",
])
def test_semantic_reranker_rejects_non_loopback_endpoints(url):
    with pytest.raises(ValueError):
        LocalOllamaEmbeddingReranker(model="test-embedding", base_url=url)


def test_semantic_reranker_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MIA_MEMORY_SEMANTIC_RERANK_ENABLED", raising=False)
    monkeypatch.setenv("MIA_MEMORY_EMBEDDING_MODEL", "some-model")
    assert LocalOllamaEmbeddingReranker.from_env() is None


def test_semantic_reranker_requires_an_explicit_model(monkeypatch):
    monkeypatch.setenv("MIA_MEMORY_SEMANTIC_RERANK_ENABLED", "true")
    monkeypatch.delenv("MIA_MEMORY_EMBEDDING_MODEL", raising=False)
    assert LocalOllamaEmbeddingReranker.from_env() is None


def test_embedding_cache_round_trips_across_memory_store_instances(tmp_path):
    import hashlib

    from mia.memory.store import MemoryStore

    path = tmp_path / "memory.sqlite3"
    first = MemoryStore(path)
    content_hash = hashlib.sha256(b"Billing decision text").hexdigest()
    first.set_embedding_cache("nomic-embed-text", content_hash, [0.25, 0.75])
    reopened = MemoryStore(path)
    assert reopened.get_embedding_cache("nomic-embed-text", content_hash) == [0.25, 0.75]


def test_embedding_cache_rejects_invalid_vectors(tmp_path):
    import hashlib

    from mia.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "memory.sqlite3")
    content_hash = hashlib.sha256(b"private text").hexdigest()
    with pytest.raises(ValueError):
        store.set_embedding_cache("model", content_hash, [float("nan")])
