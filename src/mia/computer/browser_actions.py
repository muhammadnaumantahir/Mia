"""Approval-bound consequential browser actions: click, download, upload.

Each approval is bound to the live page (URL + content digest) and to the real target element
(tag, type, label, link target, form action), so a page that changes or an element that is not what
was approved cannot be acted on. Uploads are additionally bound to the exact file content hash and
come only from owner-configured filesystem roots.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

from .approvals import ApprovalStore
from .browser import BrowserAccessError, BrowserController
from .filesystem import FilesystemAdapter

ACTION_KINDS = {"click", "download", "upload"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class BrowserActions:
    def __init__(self, browser: BrowserController, approvals: ApprovalStore,
                 filesystem: FilesystemAdapter | None = None) -> None:
        self.browser = browser
        self.approvals = approvals
        self.fs = filesystem

    def _file(self, root_id: str | None, relative_path: str | None) -> dict[str, Any]:
        if self.fs is None or not root_id or not relative_path:
            raise ValueError("root_id and relative_path are required for uploads")
        path = self.fs._resolve(root_id, relative_path)
        self.fs._validate_read_path(path)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("Regular file not found")
        size = path.stat().st_size
        if size > MAX_UPLOAD_BYTES:
            raise ValueError("Upload exceeds the 25 MB limit")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"path": path, "root_id": root_id, "relative_path": relative_path,
                "sha256": digest, "size": size}

    async def _plan(self, kind: str, ref: str, root_id: str | None,
                    relative_path: str | None) -> tuple[dict[str, Any], str, Any]:
        if kind not in ACTION_KINDS:
            raise ValueError("Unsupported browser action")
        url = self.browser.current_url
        if not url:
            raise BrowserAccessError("Browser has no active page")
        element = await self.browser.describe_ref(ref)
        if element["disabled"]:
            raise BrowserAccessError("That control is disabled")
        is_file_input = element["tag"] == "input" and element["type"] == "file"
        if kind == "click" and is_file_input:
            raise BrowserAccessError("Use the upload action for file inputs")
        if kind == "upload" and not is_file_input:
            raise BrowserAccessError("That control is not a file input")
        plan: dict[str, Any] = {
            "kind": kind, "ref": ref, "page_url": url,
            "page_digest": await self.browser.current_state_digest(),
            "element": {k: element.get(k) for k in
                        ("tag", "type", "name", "href", "form_action", "in_form")},
        }
        upload = None
        if kind == "upload":
            upload = self._file(root_id, relative_path)
            plan["file"] = {k: upload[k] for k in ("root_id", "relative_path", "sha256", "size")}
        host = urlsplit(url).hostname or url
        label = element.get("name") or element["tag"]
        flag = "CONSEQUENTIAL " if element["consequential"] or kind == "upload" else ""
        what = {
            "click": f"click '{label}'" + (" (submits a form)" if element["in_form"] else ""),
            "download": f"download via '{label}' into the approved download folder",
            "upload": f"upload local file '{relative_path}' ({upload['size'] if upload else 0} bytes) "
                      f"to {host} via '{label}'",
        }[kind]
        return plan, f"{flag}{what} on {host}", upload

    async def preview(self, kind: str, ref: str, *, root_id: str | None = None,
                      relative_path: str | None = None) -> dict[str, Any]:
        plan, summary, _ = await self._plan(kind, ref, root_id, relative_path)
        approval = self.approvals.request(
            action_id=f"browser.{kind}", payload=plan, summary=summary[:300])
        return {**approval, "plan": plan}

    async def execute(self, approval_id: str, kind: str, ref: str, *, root_id: str | None = None,
                      relative_path: str | None = None) -> dict[str, Any]:
        plan, _, upload = await self._plan(kind, ref, root_id, relative_path)
        self.approvals.consume(approval_id=approval_id, action_id=f"browser.{kind}", payload=plan)
        if kind == "click":
            return await self.browser.click_ref(ref)
        if kind == "download":
            return await self.browser.download_via(ref)
        return await self.browser.upload_file(ref, upload["path"])
