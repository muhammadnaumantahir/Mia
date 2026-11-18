"""Safe, bounded text extraction for documents inside configured filesystem roots."""

from __future__ import annotations

import hashlib
import io
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mia.computer.filesystem import FilesystemAdapter
from mia.memory.store import MemoryStore


class DocumentIngestionError(RuntimeError):
    """The source document could not be safely extracted or stored."""


class DocumentIngestor:
    MAX_EXTRACTED_CHARS = 1_000_000
    MAX_PDF_PAGES = 200
    MAX_OFFICE_ARCHIVE_ENTRIES = 10_000
    MAX_OFFICE_MEMBER_BYTES = 64 * 1024 * 1024
    MAX_OFFICE_TOTAL_BYTES = 128 * 1024 * 1024
    MAX_OFFICE_COMPRESSION_RATIO = 200

    def __init__(
        self,
        filesystem: FilesystemAdapter,
        memory: MemoryStore,
    ) -> None:
        self.filesystem = filesystem
        self.memory = memory

    def ingest(self, root_id: str, relative_path: str) -> dict[str, str | int]:
        path = self.filesystem.roots.get(root_id)
        if path is None:
            raise ValueError("Unknown filesystem root")
        source_path = (path / relative_path).resolve()
        # read_bytes performs the definitive root/symlink/size validation.
        raw = self.filesystem.read_bytes(root_id, relative_path)
        suffix = source_path.suffix.lower()
        text, parser_name = self._extract(suffix, raw)
        text = text.strip()
        if not text:
            raise DocumentIngestionError("No text could be extracted from the document")
        if len(text) > self.MAX_EXTRACTED_CHARS:
            text = text[: self.MAX_EXTRACTED_CHARS]
        file_hash = hashlib.sha256(raw).hexdigest()
        source_uri = source_path.as_uri()
        item_id = self.memory.add_item(
            kind="document",
            title=source_path.name,
            content=text,
            source_uri=source_uri,
            source_title=source_path.name,
            trust_level="untrusted",
            tags=[suffix.lstrip(".") or "document", "imported"],
            metadata={
                "root_id": root_id,
                "relative_path": str(Path(relative_path)),
                "file_sha256": file_hash,
                "file_size_bytes": len(raw),
                "parser": parser_name,
                "extracted_characters": len(text),
                "instructions_trusted": False,
            },
        )
        return {
            "item_id": item_id,
            "title": source_path.name,
            "file_sha256": file_hash,
            "extracted_characters": len(text),
            "trust_level": "untrusted",
        }

    def _validate_office_archive(self, raw: bytes) -> None:
        """Reject malformed or expansion-heavy Office ZIP containers before parsing."""
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > self.MAX_OFFICE_ARCHIVE_ENTRIES:
                    raise DocumentIngestionError("Office document contains too many archive entries")
                total_uncompressed = 0
                for entry in entries:
                    if entry.flag_bits & 0x1:
                        raise DocumentIngestionError("Encrypted Office archive entries are not supported")
                    if entry.file_size > self.MAX_OFFICE_MEMBER_BYTES:
                        raise DocumentIngestionError("Office archive entry exceeds the 64 MB limit")
                    total_uncompressed += entry.file_size
                    if total_uncompressed > self.MAX_OFFICE_TOTAL_BYTES:
                        raise DocumentIngestionError("Office archive expands beyond the 128 MB limit")
                    if entry.file_size > 1024 * 1024:
                        if entry.compress_size == 0 or (
                            entry.file_size / entry.compress_size
                            > self.MAX_OFFICE_COMPRESSION_RATIO
                        ):
                            raise DocumentIngestionError(
                                "Office archive contains an excessive compression ratio"
                            )
        except zipfile.BadZipFile as exc:
            raise DocumentIngestionError("Office document is not a valid ZIP archive") from exc
        except OSError as exc:
            raise DocumentIngestionError("Office document archive could not be inspected") from exc

    def _extract(self, suffix: str, raw: bytes) -> tuple[str, str]:
        if suffix in {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".log"}:
            try:
                return raw.decode("utf-8-sig"), "python-utf8"
            except UnicodeDecodeError as exc:
                raise DocumentIngestionError(
                    "Text file is not valid UTF-8; convert it before importing"
                ) from exc

        if suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError as exc:
                raise DocumentIngestionError(
                    'PDF import requires the optional "documents" extra.'
                ) from exc
            try:
                reader = PdfReader(io.BytesIO(raw), strict=True)
                if reader.is_encrypted:
                    raise DocumentIngestionError("Encrypted PDFs are not supported")
                parts = []
                for index, page in enumerate(reader.pages):
                    if index >= self.MAX_PDF_PAGES:
                        break
                    parts.append(page.extract_text() or "")
                    if sum(len(part) for part in parts) > self.MAX_EXTRACTED_CHARS:
                        break
                return "\n".join(parts), f"pypdf/{self._package_version('pypdf')}"
            except DocumentIngestionError:
                raise
            except Exception as exc:
                raise DocumentIngestionError("PDF extraction failed") from exc

        if suffix in {".docx", ".xlsx", ".pptx"}:
            self._validate_office_archive(raw)

        if suffix == ".docx":
            try:
                from docx import Document
            except ImportError as exc:
                raise DocumentIngestionError(
                    'DOCX import requires the optional "documents" extra.'
                ) from exc
            try:
                document = Document(io.BytesIO(raw))
                parts = [paragraph.text for paragraph in document.paragraphs]
                for table in document.tables:
                    for row in table.rows:
                        parts.append(" | ".join(cell.text for cell in row.cells))
                return "\n".join(parts), f"python-docx/{self._package_version('python-docx')}"
            except Exception as exc:
                raise DocumentIngestionError("DOCX extraction failed") from exc

        if suffix == ".xlsx":
            try:
                from openpyxl import load_workbook
            except ImportError as exc:
                raise DocumentIngestionError(
                    'XLSX import requires the optional "documents" extra.'
                ) from exc
            try:
                workbook = load_workbook(
                    io.BytesIO(raw),
                    read_only=True,
                    data_only=True,
                )
                parts = []
                for sheet in workbook.worksheets:
                    parts.append(f"Sheet: {sheet.title}")
                    for row in sheet.iter_rows(values_only=True):
                        values = [str(value) for value in row if value is not None]
                        if values:
                            parts.append(" | ".join(values))
                        if sum(len(part) for part in parts) > self.MAX_EXTRACTED_CHARS:
                            break
                workbook.close()
                return "\n".join(parts), f"openpyxl/{self._package_version('openpyxl')}"
            except Exception as exc:
                raise DocumentIngestionError("XLSX extraction failed") from exc

        if suffix == ".pptx":
            try:
                from pptx import Presentation
            except ImportError as exc:
                raise DocumentIngestionError(
                    'PPTX import requires the optional "documents" extra.'
                ) from exc
            try:
                presentation = Presentation(io.BytesIO(raw))
                parts = []
                for index, slide in enumerate(presentation.slides, start=1):
                    parts.append(f"Slide {index}")
                    for shape in slide.shapes:
                        if getattr(shape, "has_text_frame", False):
                            parts.append(shape.text)
                        if getattr(shape, "has_table", False):
                            for row in shape.table.rows:
                                parts.append(
                                    " | ".join(cell.text for cell in row.cells)
                                )
                        if sum(len(part) for part in parts) > self.MAX_EXTRACTED_CHARS:
                            break
                return "\n".join(parts), f"python-pptx/{self._package_version('python-pptx')}"
            except Exception as exc:
                raise DocumentIngestionError("PPTX extraction failed") from exc

        raise DocumentIngestionError(
            f"Unsupported document type: {suffix or 'no file extension'}"
        )

    @staticmethod
    def _package_version(package_name: str) -> str:
        try:
            return version(package_name)
        except PackageNotFoundError:
            return "unknown"
