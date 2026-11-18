from mia.memory.store import MemoryStore


def test_conversation_history_persists_in_order(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    conversation_id = store.ensure_conversation()
    store.append_message(conversation_id, "user", "Hello Mia")
    store.append_message(conversation_id, "assistant", "Hello. How can I help?")
    history = store.get_messages(conversation_id)
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert store.list_conversations()[0]["message_count"] == 2


def test_memory_search_preserves_source_provenance(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    item_id = store.add_item(
        kind="research",
        title="SPS experiment design",
        content="The Day 0 baseline must be immutable and benchmark results must be reproducible.",
        source_uri="https://example.test/research",
        source_title="Research note",
        trust_level="untrusted",
        tags=["SPS", "experiment"],
    )
    result = store.search("immutable baseline benchmark")
    assert len(result) == 1
    assert result[0]["item_id"] == item_id
    assert result[0]["source_uri"] == "https://example.test/research"
    assert result[0]["trust_level"] == "untrusted"
    assert store.get_item(item_id)["source_title"] == "Research note"


def test_memory_delete_removes_searchable_record(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    item_id = store.add_item(
        kind="note",
        title="Temporary note",
        content="Remove this from memory after testing.",
        trust_level="user",
    )
    assert store.delete_item(item_id) is True
    assert store.get_item(item_id) is None
    assert store.search("temporary note") == []



def test_memory_export_includes_conversations_provenance_and_checksum(tmp_path):
    import hashlib
    import json

    store = MemoryStore(tmp_path / "memory.sqlite3")
    conversation_id = store.ensure_conversation()
    store.append_message(conversation_id, "user", "Remember this export")
    store.append_message(conversation_id, "assistant", "I will preserve the history")
    item_id = store.add_item(
        kind="research",
        title="Export test",
        content="Source-aware knowledge remains untrusted.",
        source_uri="https://example.test/source",
        trust_level="untrusted",
        tags=["export"],
    )

    exported = store.export_data()

    assert exported["schema_version"] == 1
    assert exported["conversations"][0]["conversation_id"] == conversation_id
    assert [m["role"] for m in exported["conversations"][0]["messages"]] == [
        "user", "assistant"
    ]
    exported_item = next(item for item in exported["memory_items"] if item["item_id"] == item_id)
    assert exported_item["source_uri"] == "https://example.test/source"
    assert exported_item["trust_level"] == "untrusted"
    digest_payload = {key: value for key, value in exported.items() if key != "sha256"}
    canonical = json.dumps(
        digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert exported["sha256"] == hashlib.sha256(canonical).hexdigest()
