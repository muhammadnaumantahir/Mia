from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.knowledge.document_ingestion import DocumentIngestor
from mia.memory.store import MemoryStore


def test_markdown_document_is_indexed_with_hash_and_untrusted_source(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    source = root / "research.md"
    source.write_text(
        "# Research\n\nThe SPS framework preserves ten canonical layers.",
        encoding="utf-8",
    )
    filesystem = FilesystemAdapter(
        [root],
        ApprovalStore(tmp_path / "approvals.sqlite3"),
    )
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    ingestor = DocumentIngestor(filesystem, memory)

    result = ingestor.ingest("root_1", "research.md")
    item = memory.get_item(result["item_id"])

    assert result["trust_level"] == "untrusted"
    assert item["trust_level"] == "untrusted"
    assert item["metadata"]["instructions_trusted"] is False
    assert item["metadata"]["file_sha256"] == result["file_sha256"]
    assert "ten canonical layers" in item["content"]
    assert memory.search("canonical layers")[0]["item_id"] == result["item_id"]


def test_unsupported_document_type_is_rejected(tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    (root / "script.exe").write_bytes(b"not a document")
    filesystem = FilesystemAdapter(
        [root],
        ApprovalStore(tmp_path / "approvals.sqlite3"),
    )
    ingestor = DocumentIngestor(
        filesystem,
        MemoryStore(tmp_path / "memory.sqlite3"),
    )
    try:
        ingestor.ingest("root_1", "script.exe")
    except RuntimeError as exc:
        assert "Unsupported document type" in str(exc)
    else:
        raise AssertionError("Unsupported files must be rejected")
