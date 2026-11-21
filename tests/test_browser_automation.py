"""Real-browser tests. Set MIA_TEST_BROWSER to a Chromium executable if Playwright's is absent."""

import asyncio
import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.browser import BrowserAccessError, BrowserController
from mia.computer.browser_actions import BrowserActions
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

EXE = os.environ.get("MIA_TEST_BROWSER") or None
ARGS = ("--no-sandbox", "--disable-gpu")
FILE_BODY = b"report-contents-12345"


def _browser_available() -> bool:
    if EXE:
        return os.path.exists(EXE)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            return os.path.exists(p.chromium.executable_path)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _browser_available(), reason="no Chromium available")

PAGES = {
    "/": """<html><head><title>Home</title></head><body><h1>Home</h1>
      <a href="/form">Go to form</a> <a href="/popup" target="_blank">Open popup</a>
      <button id="noop">Do nothing</button>
      <button id="reveal" onclick="document.body.insertAdjacentHTML('beforeend','<p>Revealed!</p>')">Reveal</button>
      </body></html>""",
    "/popup": "<html><head><title>Popup</title></head><body><h1>Popup page</h1></body></html>",
    "/form": """<html><head><title>Form</title></head><body><h1>Order form</h1>
      <form method="POST" action="/submit"><label for="q">Your name</label>
      <input id="q" name="q" type="text" placeholder="name">
      <input name="pw" type="password" value="hunter2-secret">
      <input name="card" autocomplete="cc-number" type="text">
      <select name="size"><option>Small</option><option>Large</option></select>
      <label><input type="checkbox" name="gift"> Gift wrap</label>
      <button type="submit">Place order</button></form>
      <a href="/file.txt">Download report</a> <a href="/installer.exe">Get installer</a>
      <form method="POST" action="/upload" enctype="multipart/form-data">
      <input type="file" name="doc"><button type="submit">Send document</button></form>
      </body></html>""",
    "/table": """<html><body><h1>Prices</h1><h2>Fruit</h2><table><tr><th>Item</th><th>Cost</th></tr>
      <tr><td>Apple</td><td>1</td></tr><tr><td>Pear</td><td>2</td></tr></table>
      <ul><li>one</li><li>two</li></ul></body></html>""",
    "/long": "<html><body>" + "<p>line</p>" * 400 + "</body></html>",
    "/autodl": """<html><body><a id="a" href="/file.txt" download>x</a>
      <script>document.getElementById('a').click()</script><p>auto</p></body></html>""",
}


class Handler(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype="text/html", headers=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in PAGES:
            return self._send(PAGES[path].encode())
        if path == "/file.txt":
            return self._send(FILE_BODY, "text/plain",
                              {"Content-Disposition": 'attachment; filename="report.txt"'})
        if path == "/installer.exe":
            return self._send(b"MZ", "application/octet-stream",
                              {"Content-Disposition": 'attachment; filename="installer.exe"'})
        if path == "/cookie":
            return self._send(b"<html><body>cookie set</body></html>",
                              headers={"Set-Cookie": "sess=abc; Max-Age=3600; Path=/"})
        if path == "/whoami":
            has = "sess=abc" in (self.headers.get("Cookie") or "")
            return self._send(f"<html><body>session:{'abc' if has else 'none'}</body></html>".encode())
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        path = urlsplit(self.path).path
        self.state.setdefault(path, []).append(body)
        if path == "/submit":
            return self._send(b"<html><head><title>Thanks</title></head><body>Submitted order</body></html>")
        if path == "/upload":
            return self._send(b"<html><head><title>Uploaded</title></head><body>Upload received</body></html>")
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def server():
    Handler.state = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", Handler.state
    httpd.shutdown()


def policy_for(base):
    port = urlsplit(base).port

    def policy(url):
        parts = urlsplit(url)
        if parts.hostname == "127.0.0.1" and parts.port == port:
            return url
        raise BrowserAccessError("blocked by test policy")
    return policy


def make(base, tmp_path, **kw):
    return BrowserController(
        executable_path=EXE, launch_args=ARGS, url_policy=policy_for(base),
        download_dir=tmp_path / "dl", screenshot_dir=tmp_path / "shots", **kw)


def go(coro):
    return asyncio.run(coro)


def approve_and_run(approvals, actions, kind, ref, **kw):
    async def inner():
        preview = await actions.preview(kind, ref, **kw)
        approvals.approve(preview["approval_id"])
        return await actions.execute(preview["approval_id"], kind, ref, **kw)
    return inner()


def ref_for(snapshot, name=None, **match):
    for el in snapshot["elements"]:
        if name is not None and name.casefold() not in el["name"].casefold():
            continue
        if all(el.get(k) == v for k, v in match.items()):
            return el["ref"]
    raise AssertionError(f"element not found: {name} {match}")


def test_observe_find_extract_and_secret_handling(server, tmp_path):
    base, _ = server

    async def scenario():
        b = make(base, tmp_path)
        try:
            await b.navigate(base + "/form")
            snap = await b.observe()
            assert snap["content_trust"] == "untrusted_external_web_content"
            pw = next(e for e in snap["elements"] if e["type"] == "password")
            assert pw["value"] is None and pw["fillable"] is False
            assert "hunter2" not in str(snap)
            submit = next(e for e in snap["elements"] if e["name"] == "Place order")
            assert submit["consequential"] is True
            name_field = next(e for e in snap["elements"] if e["name"] == "Your name")
            assert name_field["fillable"] and not next(
                e for e in snap["elements"] if e.get("autocomplete") == "cc-number")["fillable"]
            assert [e["name"] for e in await b.find("download")] == ["Download report"]
            await b.navigate(base + "/table")
            data = await b.extract()
            assert data["tables"][0]["headers"] == ["Item", "Cost"]
            assert data["tables"][0]["rows"] == [["Apple", "1"], ["Pear", "2"]]
            assert {"level": 1, "text": "Prices"} in data["headings"]
            assert ["one", "two"] in data["lists"]
        finally:
            await b.close()
    go(scenario())


def test_fill_select_check_are_verified_and_guarded(server, tmp_path):
    base, _ = server

    async def scenario():
        b = make(base, tmp_path)
        try:
            await b.navigate(base + "/form")
            snap = await b.observe()
            r = await b.fill(ref_for(snap, "Your name"), "Ada")
            assert r["verified"] is True
            with pytest.raises(BrowserAccessError):
                await b.fill(ref_for(snap, type="password"), "x")
            with pytest.raises(BrowserAccessError):
                await b.fill(next(e["ref"] for e in snap["elements"]
                                  if e.get("autocomplete") == "cc-number"), "4111")
            with pytest.raises(BrowserAccessError):
                await b.fill(ref_for(snap, "Your name"), "key AKIAABCDEFGHIJKLMNOP")
            with pytest.raises(BrowserAccessError):
                await b.fill(ref_for(snap, "Place order"), "x")
            sel = ref_for(snap, tag="select")
            assert (await b.select_option(sel, "Large"))["verified"] is True
            box = ref_for(snap, type="checkbox")
            assert (await b.set_checked(box, True))["checked"] is True
            with pytest.raises(BrowserAccessError):
                await b.set_checked(ref_for(snap, "Your name"), True)
            with pytest.raises(ValueError):
                await b.fill("bad-ref", "x")
            await b.navigate(base + "/")
            with pytest.raises(BrowserAccessError, match="stale"):
                await b.fill(ref_for(snap, "Your name"), "again")
        finally:
            await b.close()
    go(scenario())


def test_approved_click_submits_form_and_effect_is_verified(server, tmp_path):
    base, state = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")

    async def scenario():
        b = make(base, tmp_path)
        actions = BrowserActions(b, approvals)
        try:
            await b.navigate(base + "/form")
            snap = await b.observe()
            await b.fill(ref_for(snap, "Your name"), "Grace")
            ref = ref_for(snap, "Place order")
            preview = await actions.preview("click", ref)
            assert preview["summary"].startswith("CONSEQUENTIAL")
            with pytest.raises(ApprovalError):  # not approved yet
                await actions.execute(preview["approval_id"], "click", ref)
            approvals.approve(preview["approval_id"])
            assert not state.get("/submit")
            result = await actions.execute(preview["approval_id"], "click", ref)
            assert result["verified"] and result["effect"]["url_changed"]
            assert result["title"] == "Thanks"
            assert b"q=Grace" in state["/submit"][-1]  # the real server saw the real submission
            with pytest.raises((ApprovalError, BrowserAccessError)):  # replay is refused
                await actions.execute(preview["approval_id"], "click", ref)
        finally:
            await b.close()
    go(scenario())


def test_approval_invalid_when_page_or_target_changes(server, tmp_path):
    base, _ = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")

    async def scenario():
        b = make(base, tmp_path)
        actions = BrowserActions(b, approvals)
        try:
            await b.navigate(base + "/")
            snap = await b.observe()
            go_ref = ref_for(snap, "Go to form")
            pv = await actions.preview("click", go_ref)
            approvals.approve(pv["approval_id"])
            await b.click_ref(ref_for(snap, "Reveal"))  # page content changes after approval
            snap2 = await b.observe()
            with pytest.raises(ApprovalError):
                await actions.execute(pv["approval_id"], "click", ref_for(snap2, "Go to form"))
            # approval for one element cannot be used on another
            pv2 = await actions.preview("click", ref_for(snap2, "Go to form"))
            approvals.approve(pv2["approval_id"])
            with pytest.raises(ApprovalError):
                await actions.execute(pv2["approval_id"], "click", ref_for(snap2, "Open popup"))
            with pytest.raises(ApprovalError):  # and an approved click is not a download
                await actions.execute(pv2["approval_id"], "download", ref_for(snap2, "Go to form"))
        finally:
            await b.close()
    go(scenario())


def test_click_without_observable_effect_is_not_reported_verified(server, tmp_path):
    base, _ = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")

    async def scenario():
        b = make(base, tmp_path)
        actions = BrowserActions(b, approvals)
        try:
            await b.navigate(base + "/")
            snap = await b.observe()
            r = await approve_and_run(approvals, actions, "click", ref_for(snap, "Do nothing"))
            assert r["verified"] is False and "no page change" in r["note"]
            snap = await b.observe()
            r = await approve_and_run(approvals, actions, "click", ref_for(snap, "Reveal"))
            assert r["verified"] is True and r["effect"]["content_changed"]
        finally:
            await b.close()
    go(scenario())


def test_tabs(server, tmp_path):
    base, _ = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")

    async def scenario():
        b = make(base, tmp_path)
        actions = BrowserActions(b, approvals)
        try:
            await b.navigate(base + "/")
            snap = await b.observe()
            r = await approve_and_run(approvals, actions, "click", ref_for(snap, "Open popup"))
            assert r["effect"]["new_tab"] and r["title"] == "Popup"
            tabs = await b.list_tabs()
            assert [t["active"] for t in tabs] == [False, True] and len(tabs) == 2
            assert (await b.switch_tab(0))["title"] == "Home"
            created = await b.new_tab(base + "/table")
            assert created["index"] == 2
            assert (await b.close_tab(2))["closed"] == 2
            assert len(await b.list_tabs()) == 2
            with pytest.raises(ValueError):
                await b.switch_tab(9)
            with pytest.raises(BrowserAccessError):
                await b.new_tab("https://example.com")
            await b.close_tab(1)
            with pytest.raises(BrowserAccessError, match="last tab"):
                await b.close_tab(0)
        finally:
            await b.close()
    go(scenario())


def test_scroll_and_screenshot(server, tmp_path):
    base, _ = server

    async def scenario():
        b = make(base, tmp_path)
        try:
            await b.navigate(base + "/long")
            r = await b.scroll("down", 800)
            assert r["verified"] and r["after"] > r["before"]
            assert (await b.scroll("bottom"))["after"] > r["after"]
            assert (await b.scroll("down", 500))["verified"] is False
            assert (await b.scroll("top"))["after"] == 0
            with pytest.raises(ValueError):
                await b.scroll("sideways")
            shot = await b.screenshot()
            data = open(shot["path"], "rb").read()
            assert data.startswith(b"\x89PNG") and shot["verified"]
        finally:
            await b.close()
    go(scenario())


def test_downloads_only_through_approved_action(server, tmp_path):
    base, _ = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")

    async def scenario():
        b = make(base, tmp_path)
        actions = BrowserActions(b, approvals)
        try:
            await b.navigate(base + "/autodl")  # page tries to download by itself
            await asyncio.sleep(1)
            assert not (tmp_path / "dl").exists() or not list((tmp_path / "dl").iterdir())

            await b.navigate(base + "/form")
            snap = await b.observe()
            r = await approve_and_run(approvals, actions, "download", ref_for(snap, "Download report"))
            assert r["filename"] == "report.txt" and r["verified"]
            assert r["sha256"] == hashlib.sha256(FILE_BODY).hexdigest()
            assert (tmp_path / "dl" / "report.txt").read_bytes() == FILE_BODY
            r2 = await approve_and_run(approvals, actions, "download", ref_for(snap, "Download report"))
            assert r2["filename"] == "report-1.txt"  # never overwrites

            with pytest.raises(BrowserAccessError, match="executable"):
                await approve_and_run(approvals, actions, "download", ref_for(snap, "Get installer"))
            assert not (tmp_path / "dl" / "installer.exe").exists()
        finally:
            await b.close()
    go(scenario())


def test_upload_bound_to_file_hash_and_goes_to_real_server(server, tmp_path):
    base, state = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    root = tmp_path / "root"
    fs = FilesystemAdapter([root], approvals)
    (root / "doc.txt").write_text("my-document-body", encoding="utf-8")
    (root / ".env").write_text("TOKEN=x")

    async def scenario():
        b = make(base, tmp_path)
        actions = BrowserActions(b, approvals, fs)
        try:
            await b.navigate(base + "/form")
            snap = await b.observe()
            file_ref = ref_for(snap, type="file") if any(
                e["type"] == "file" for e in snap["elements"]) else None
            assert file_ref, "file input should be observable"
            kw = {"root_id": "root_1", "relative_path": "doc.txt"}
            pv = await actions.preview("upload", file_ref, **kw)
            assert pv["summary"].startswith("CONSEQUENTIAL")
            approvals.approve(pv["approval_id"])
            (root / "doc.txt").write_text("tampered-after-approval", encoding="utf-8")
            with pytest.raises(ApprovalError):
                await actions.execute(pv["approval_id"], "upload", file_ref, **kw)
            (root / "doc.txt").write_text("my-document-body", encoding="utf-8")
            res = await actions.execute(pv["approval_id"], "upload", file_ref, **kw)
            assert res["verified"] and res["attached"] == ["doc.txt"]

            with pytest.raises(Exception, match="credential|blocked|not allowed|Access"):
                await actions.preview("upload", file_ref, root_id="root_1", relative_path=".env")
            with pytest.raises(BrowserAccessError):  # upload action on a non-file control
                await actions.preview("upload", ref_for(snap, "Place order"), **kw)
            with pytest.raises(BrowserAccessError):  # click on a file input must use upload
                await actions.preview("click", file_ref)

            send = ref_for(snap, "Send document")
            r = await approve_and_run(approvals, actions, "click", send)
            assert r["verified"] and r["title"] == "Uploaded"
            assert b"my-document-body" in state["/upload"][-1]
        finally:
            await b.close()
    go(scenario())


def _browser_persists_cookies(tmp_path) -> bool:
    """Some minimal Chromium builds (e.g. headless shells) never write the cookie store."""
    import time

    from playwright.async_api import async_playwright

    async def run(add):
        async with async_playwright() as p:
            kw = {"executable_path": EXE} if EXE else {}
            ctx = await p.chromium.launch_persistent_context(
                str(tmp_path / "probe"), headless=True, args=list(ARGS), **kw)
            if add:
                await ctx.add_cookies([{"name": "k", "value": "v", "domain": "example.com",
                                        "path": "/", "expires": time.time() + 3600}])
            found = await ctx.cookies()
            await ctx.close()
            return found

    go(run(True))
    return bool(go(run(False)))


def test_session_works_without_exposing_cookies_and_isolated_profile_is_separate(server, tmp_path):
    base, _ = server
    profile = tmp_path / "profile"

    async def scenario():
        b = make(base, tmp_path, profile_dir=profile)
        try:
            await b.navigate(base + "/cookie")
            snap = await b.observe()
            assert "sess" not in str(snap) and "abc" not in str(snap)
            assert "session:abc" in (await b.navigate(base + "/whoami"))["text"]
            for name in ("cookies", "get_cookies", "storage_state", "export_session"):
                assert not hasattr(b, name)
            assert (profile / "Default").is_dir()  # Mia's own profile, not the user's browser
        finally:
            await b.close()
        isolated = make(base, tmp_path)  # no profile: fresh and separate
        try:
            assert "session:none" in (await isolated.navigate(base + "/whoami"))["text"]
        finally:
            await isolated.close()
    go(scenario())


def test_profile_session_survives_restart(server, tmp_path):
    base, _ = server
    if not _browser_persists_cookies(tmp_path):
        pytest.skip("this Chromium build does not persist cookies across restarts; "
                    "run on a full Chrome/Chromium to verify")
    profile = tmp_path / "profile2"

    async def scenario():
        first = make(base, tmp_path, profile_dir=profile)
        try:
            await first.navigate(base + "/cookie")
        finally:
            await first.close()
        second = make(base, tmp_path, profile_dir=profile)
        try:
            assert "session:abc" in (await second.navigate(base + "/whoami"))["text"]
        finally:
            await second.close()
    go(scenario())


def test_url_policy_enforced_for_navigation_and_default_blocks_loopback(server, tmp_path):
    base, _ = server

    async def scenario():
        b = make(base, tmp_path)
        try:
            with pytest.raises(BrowserAccessError):
                await b.navigate("http://example.com/")
        finally:
            await b.close()
        default = BrowserController(executable_path=EXE, launch_args=ARGS)
        try:
            with pytest.raises(BrowserAccessError):
                await default.navigate(base + "/")  # loopback is blocked by default
        finally:
            await default.close()
    go(scenario())


def test_browser_api_end_to_end(server, tmp_path):
    base, state = server
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    browser = make(base, tmp_path)
    app = create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"), approvals=approvals,
                     browser=browser)
    with TestClient(app) as client:
        assert client.post("/api/browser/navigate", json={"url": base + "/form"}).status_code == 200
        obs = client.get("/api/browser/observe").json()
        name = next(e["ref"] for e in obs["elements"] if e["name"] == "Your name")
        pw = next(e["ref"] for e in obs["elements"] if e["type"] == "password")
        order = next(e["ref"] for e in obs["elements"] if e["name"] == "Place order")
        # Fill, scroll and screenshot are fail-closed until the owner grants them (see test_browser_api.py).
        blocked = client.post("/api/browser/fill", json={"ref": name, "text": "Api User"})
        assert blocked.status_code == 409
        granted = client.post("/api/computer/permissions", json={
            "capabilities": ["browser.fill", "browser.scroll", "browser.screenshot"], "level": "interact", "kind": "session"})
        assert granted.status_code == 201, granted.text
        assert client.post("/api/browser/fill", json={"ref": name, "text": "Api User"}).json()["verified"]
        assert client.post("/api/browser/fill", json={"ref": pw, "text": "x"}).status_code == 403
        assert client.post("/api/browser/fill", json={"ref": "nope", "text": "x"}).status_code == 422
        assert client.get("/api/browser/find", params={"query": "order"}).json()["items"]
        assert client.post("/api/browser/scroll", json={"direction": "sideways"}).status_code == 422
        assert client.get("/api/browser/tabs").json()["items"][0]["active"] is True
        shot = client.post("/api/browser/screenshot").json()
        assert shot["bytes"] > 0

        body = {"kind": "click", "ref": order}
        pv = client.post("/api/browser/actions/preview", json=body)
        assert pv.status_code == 201 and pv.json()["summary"].startswith("CONSEQUENTIAL")
        aid = pv.json()["approval_id"]
        assert client.post("/api/browser/actions/execute",
                           json={**body, "approval_id": aid}).status_code == 409
        client.post(f"/api/computer/approvals/{aid}/approve")
        done = client.post("/api/browser/actions/execute", json={**body, "approval_id": aid})
        assert done.status_code == 200 and done.json()["verified"] is True
        assert b"q=Api+User" in state["/submit"][-1]
        assert client.post("/api/browser/close").status_code == 200
