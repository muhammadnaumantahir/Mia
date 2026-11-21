"""Guard: every Python source/test file must be syntactically valid.

A single stray character in one module can make whole packages un-importable and hide
as dozens of unrelated collection errors, so this fails fast with the exact file/line.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = sorted(p for base in ("src", "tests", "scripts") if (ROOT / base).exists()
               for p in (ROOT / base).rglob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_python_file_compiles(path: Path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")
