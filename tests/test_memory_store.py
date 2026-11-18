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
