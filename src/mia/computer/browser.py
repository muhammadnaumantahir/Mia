"""Isolated Playwright browser adapter with public-network guardrails."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from mia.memory.safety import find_secret


class BrowserAccessError(PermissionError):
    """A browser request is outside the public-web navigation policy."""


def validate_public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise BrowserAccessError("Only HTTP and HTTPS URLs are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise BrowserAccessError("URL must have a valid public hostname and no embedded credentials")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(
        (".localhost", ".local", ".internal", ".test", ".invalid")
    ):
        raise BrowserAccessError("Local and reserved hostnames are blocked")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        try:
            records = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise BrowserAccessError("Hostname could not be resolved") from exc
        addresses = {record[4][0].split("%", 1)[0] for record in records}
        if not addresses:
            raise BrowserAccessError("Hostname did not resolve to an address")
        for raw_address in addresses:
            parsed_address = ipaddress.ip_address(raw_address)
            if not parsed_address.is_global:
                raise BrowserAccessError("Private, loopback and reserved network targets are blocked")
    else:
        if not address.is_global:
            raise BrowserAccessError("Private, loopback and reserved network targets are blocked")

    return url


_BLOCKED_DOWNLOAD_SUFFIXES = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", ".wsf", ".scr", ".dll",
    ".com", ".jar", ".apk", ".app", ".dmg", ".pkg", ".sh", ".lnk", ".reg", ".hta",
}
_CONSEQUENTIAL = re.compile(
    r"\b(buy|purchase|pay|order|checkout|place|send|post|publish|submit|delete|remove|confirm|"
    r"sign|transfer|subscribe|donate|book|reserve|apply|upload|share|tweet|comment|reply)\b", re.I)
_SECRET_AUTOCOMPLETE = ("password", "cc-", "one-time-code")

_OBSERVE_JS = r"""() => {
  document.querySelectorAll('[data-mia-ref]').forEach(e => e.removeAttribute('data-mia-ref'));
  const sel = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=checkbox],' +
              '[role=tab],[role=menuitem],summary,[contenteditable=""],[contenteditable=true]';
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = e => {
    let t = e.getAttribute('aria-label') || '';
    if (!t && e.labels && e.labels.length) t = Array.from(e.labels).map(l => l.innerText).join(' ');
    if (!t) t = e.innerText || e.getAttribute('alt') || e.getAttribute('title') ||
                e.getAttribute('placeholder') ||
                (e.tagName === 'INPUT' && ['submit','button','reset'].includes(e.type) ? e.value : '') || '';
    return t.trim().replace(/\s+/g, ' ').slice(0, 120); };
  const out = []; let n = 0;
  for (const e of document.querySelectorAll(sel)) {
    if (n >= 150) break;
    if (!vis(e) || e.type === 'hidden') continue;
    const ref = 'e' + (++n);
    e.setAttribute('data-mia-ref', ref);
    const tag = e.tagName.toLowerCase();
    const type = (e.getAttribute('type') || '').toLowerCase();
    const info = {ref, tag, type, role: e.getAttribute('role') || '', name: label(e),
                  disabled: !!e.disabled, in_form: !!e.form};
    if (e.form) info.form_action = (e.form.getAttribute('action') || '').slice(0, 200);
    if (tag === 'a') info.href = e.href;
    if (['input', 'textarea', 'select'].includes(tag)) {
      info.field_name = e.getAttribute('name') || '';
      info.autocomplete = (e.getAttribute('autocomplete') || '').toLowerCase();
      if (type === 'password') info.value = null;
      else if (type === 'checkbox' || type === 'radio') info.checked = e.checked;
      else if (type !== 'file') info.value = (e.value || '').slice(0, 80);
      if (tag === 'select') info.options = Array.from(e.options).slice(0, 30).map(o => o.label || o.value);
    }
    out.push(info);
  }
  return out;
}"""

_EXTRACT_JS = r"""() => {
  const txt = e => (e.innerText || '').trim().replace(/\s+/g, ' ');
  const headings = Array.from(document.querySelectorAll('h1,h2,h3')).slice(0, 40)
      .map(h => ({level: Number(h.tagName[1]), text: txt(h).slice(0, 200)})).filter(h => h.text);
  const tables = Array.from(document.querySelectorAll('table')).slice(0, 5).map(t => {
    const rows = Array.from(t.querySelectorAll('tr')).slice(0, 50)
        .map(r => Array.from(r.querySelectorAll('th,td')).slice(0, 20).map(c => txt(c).slice(0, 120)));
    return {headers: rows.length ? rows[0] : [], rows: rows.slice(1)};
  });
  const lists = Array.from(document.querySelectorAll('ul,ol')).slice(0, 8)
      .map(l => Array.from(l.querySelectorAll(':scope > li')).slice(0, 30).map(i => txt(i).slice(0, 160)));
  return {headings, tables, lists};
}"""


class BrowserController:
    """Own one isolated browser context (optionally with a persistent Mia-only profile).

    It never attaches to the user's default browser profile and never exposes cookies,
    storage or passwords. Every network request, including redirects and sub-requests of every
    tab, passes the URL policy (public web only by default). Downloads are refused unless an
    approved download action is in progress.
    """

    MAX_TEXT = 30_000

    def __init__(
        self,
        *,
        headless: bool = True,
        executable_path: str | None = None,
        launch_args: tuple[str, ...] = (),
        url_policy: Callable[[str], str] | None = None,
        profile_dir: str | Path | None = None,
        download_dir: str | Path | None = None,
        screenshot_dir: str | Path | None = None,
    ) -> None:
        self.headless = headless
        self.executable_path = executable_path
        self.launch_args = tuple(launch_args)
        self._url_policy = url_policy or validate_public_url
        self.profile_dir = Path(profile_dir) if profile_dir else None
        self.download_dir = Path(download_dir) if download_dir else None
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._pages: list[Any] = []
        self._expecting_download = False

    @property
    def is_running(self) -> bool:
        return self._page is not None

    @property
    def current_url(self) -> str | None:
        return self._page.url if self._page is not None else None

    async def start(self) -> None:
        if self.is_running:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                'Install Mia with the "computer" extra and run "playwright install chromium".'
            ) from exc
        self._playwright = await async_playwright().start()
        try:
            options: dict[str, Any] = {"headless": self.headless, "args": list(self.launch_args)}
            if self.executable_path:
                options["executable_path"] = self.executable_path
            if self.profile_dir is not None:
                self.profile_dir.mkdir(parents=True, exist_ok=True)
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(self.profile_dir), accept_downloads=True, **options)
            else:
                self._browser = await self._playwright.chromium.launch(**options)
                self._context = await self._browser.new_context(
                    accept_downloads=True, java_script_enabled=True)
            await self._context.route("**/*", self._guard_request)
            self._context.on("page", self._on_new_page)
            existing = [p for p in self._context.pages if not p.is_closed()]
            page = existing[0] if existing else await self._context.new_page()
            self._pages = [p for p in self._context.pages if not p.is_closed()] or [page]
            for known in self._pages:
                self._watch(known)
            self._page = page
        except Exception:
            await self.close()
            raise

    def _watch(self, page: Any) -> None:
        page.on("download", self._on_download)

    def _on_new_page(self, page: Any) -> None:
        if page not in self._pages:
            self._pages.append(page)
            self._watch(page)

    def _on_download(self, download: Any) -> None:
        if not self._expecting_download:
            asyncio.ensure_future(download.cancel())

    async def _guard_request(self, route) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._url_policy, route.request.url), timeout=5)
        except Exception:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    def _require_page(self) -> Any:
        if self._page is None or self._page.is_closed():
            raise BrowserAccessError("Browser has no active page")
        return self._page

    async def current_state_digest(self) -> str:
        page = self._require_page()
        title = await page.title()
        text = await page.locator("body").inner_text(timeout=5_000)
        material = page.url + "\\n" + title + "\\n" + text[:30_000]
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def _settle(self) -> None:
        page = self._require_page()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8_000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=2_000)
        except Exception:
            pass  # long-polling pages never go idle; that is not a failure

    async def navigate(self, url: str) -> dict[str, object]:
        await self.start()
        await asyncio.wait_for(asyncio.to_thread(self._url_policy, url), timeout=5)
        page = self._require_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await self._settle()
        title = await page.title()
        text = await page.locator("body").inner_text(timeout=5_000)
        links = await page.locator("a[href]").evaluate_all(
            """nodes => nodes.slice(0, 100).map(a => ({
                text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 180),
                href: a.href
            }))"""
        )
        return {
            "url": page.url,
            "title": title[:300],
            "status": response.status if response else None,
            "text": text[: self.MAX_TEXT],
            "links": links,
        }

    async def search(self, query: str) -> dict[str, object]:
        if not query.strip() or len(query) > 500:
            raise ValueError("Search query must be 1-500 characters")
        search_url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
        result = await self.navigate(search_url)
        result["query"] = query
        result["content_trust"] = "untrusted_external_web_content"
        return result

    # ---- observation (read-only) ---------------------------------------------------------
    async def observe(self) -> dict[str, Any]:
        page = self._require_page()
        await self._settle()
        elements = await page.evaluate(_OBSERVE_JS)
        title = await page.title()
        text = await page.locator("body").inner_text(timeout=5_000)
        for el in elements:
            el["consequential"] = self._is_consequential(el)
            el["fillable"] = self._is_fillable(el)
        return {
            "url": page.url, "title": title[:300], "text": text[:8_000],
            "elements": elements, "truncated": len(elements) >= 150,
            "content_trust": "untrusted_external_web_content",
        }

    async def find(self, query: str) -> list[dict[str, Any]]:
        if not query.strip() or len(query) > 200:
            raise ValueError("Query must be 1-200 characters")
        needle = query.casefold()
        snapshot = await self.observe()
        return [
            el for el in snapshot["elements"]
            if needle in " ".join(str(el.get(k, "")) for k in ("name", "field_name", "href", "tag",
                                                              "type", "role")).casefold()
        ][:50]

    async def extract(self) -> dict[str, Any]:
        page = self._require_page()
        await self._settle()
        data = await page.evaluate(_EXTRACT_JS)
        data.update(url=page.url, title=(await page.title())[:300],
                    content_trust="untrusted_external_web_content")
        return data

    @staticmethod
    def _is_fillable(el: dict[str, Any]) -> bool:
        if el["disabled"]:
            return False
        if el["tag"] == "textarea":
            return True
        if el["tag"] == "input":
            if el["type"] in {"password", "file", "checkbox", "radio", "submit", "button", "reset",
                              "image", "hidden", "range", "color"}:
                return False
            return not any(m in el.get("autocomplete", "") for m in _SECRET_AUTOCOMPLETE)
        return False

    @staticmethod
    def _is_consequential(el: dict[str, Any]) -> bool:
        is_submit = el["type"] in {"submit", "image"} or (
            el["tag"] == "button" and el["in_form"] and el["type"] in {"", "submit"})
        return bool(is_submit or (el["tag"] in {"button", "a"} and _CONSEQUENTIAL.search(
            el.get("name", "")))) or el["type"] == "file"

    async def _ref(self, ref: str):
        if not re.fullmatch(r"e\d{1,3}", ref or ""):
            raise ValueError("Invalid element reference; call observe first")
        locator = self._require_page().locator(f'[data-mia-ref="{ref}"]')
        if await locator.count() != 1:
            raise BrowserAccessError("That element reference is stale; observe the page again")
        return locator

    async def describe_ref(self, ref: str) -> dict[str, Any]:
        """Fresh facts about one element, used to bind approvals to the real target."""
        locator = await self._ref(ref)
        info = await locator.evaluate(
            """e => ({tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
              name: ((e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('title') || '')
                     .trim().replace(/\\s+/g, ' ').slice(0, 120)),
              href: e.tagName === 'A' ? e.href : '', in_form: !!e.form,
              form_action: e.form ? (e.form.getAttribute('action') || '').slice(0, 200) : '',
              disabled: !!e.disabled})"""
        )
        info["ref"] = ref
        info["consequential"] = self._is_consequential(info)
        return info

    # ---- low-risk page interaction (no external side effects) --------------------------
    async def fill(self, ref: str, text: str) -> dict[str, Any]:
        if not isinstance(text, str) or len(text) > 5000:
            raise ValueError("text must be at most 5000 characters")
        if find_secret(text):
            raise BrowserAccessError("Refusing to type text that looks like a secret into a web page")
        locator = await self._ref(ref)
        info = await self.describe_ref(ref)
        info.update(autocomplete=(await locator.get_attribute("autocomplete") or "").lower())
        if not self._is_fillable({**info, "disabled": info["disabled"]}):
            raise BrowserAccessError(
                "That control cannot be filled (password, payment, file, button or disabled)")
        await locator.fill(text, timeout=5_000)
        actual = await locator.input_value(timeout=3_000)
        return {"ref": ref, "verified": actual == text, "length": len(actual),
                "note": None if actual == text else "The page changed the value after typing."}

    async def select_option(self, ref: str, value: str) -> dict[str, Any]:
        locator = await self._ref(ref)
        if await locator.evaluate("e => e.tagName.toLowerCase()") != "select":
            raise BrowserAccessError("That control is not a drop-down list")
        try:
            await locator.select_option(label=value, timeout=3_000)
        except Exception:
            await locator.select_option(value=value, timeout=3_000)
        chosen = await locator.evaluate(
            "e => ({value: e.value, label: e.selectedOptions[0] ? e.selectedOptions[0].label : ''})")
        return {"ref": ref, "selected": chosen, "verified": value in {chosen["value"], chosen["label"]}}

    async def set_checked(self, ref: str, checked: bool) -> dict[str, Any]:
        locator = await self._ref(ref)
        if await locator.evaluate("e => e.type") not in {"checkbox", "radio"}:
            raise BrowserAccessError("That control is not a checkbox or radio button")
        await locator.set_checked(bool(checked), timeout=3_000)
        state = await locator.is_checked()
        return {"ref": ref, "checked": state, "verified": state == bool(checked)}

    async def scroll(self, direction: str = "down", amount: int = 600) -> dict[str, Any]:
        if direction not in {"up", "down", "top", "bottom"}:
            raise ValueError("direction must be up, down, top or bottom")
        if not 1 <= amount <= 5000:
            raise ValueError("amount must be between 1 and 5000 pixels")
        page = self._require_page()
        before = await page.evaluate("() => window.scrollY")
        script = {
            "down": f"() => window.scrollBy(0, {int(amount)})",
            "up": f"() => window.scrollBy(0, -{int(amount)})",
            "top": "() => window.scrollTo(0, 0)",
            "bottom": "() => window.scrollTo(0, document.documentElement.scrollHeight)",
        }[direction]
        await page.evaluate(script)
        await page.wait_for_timeout(150)
        after = await page.evaluate("() => window.scrollY")
        moved = abs(after - before) > 0
        return {"before": before, "after": after, "verified": moved,
                "note": None if moved else "The page did not scroll (already at the edge or not scrollable)."}

    # ---- tabs --------------------------------------------------------------------------------
    def _live_pages(self) -> list[Any]:
        self._pages = [p for p in self._pages if not p.is_closed()]
        return self._pages

    async def list_tabs(self) -> list[dict[str, Any]]:
        tabs = []
        for index, page in enumerate(self._live_pages()):
            try:
                title = (await page.title())[:200]
            except Exception:
                title = ""
            tabs.append({"index": index, "url": page.url, "title": title,
                         "active": page is self._page})
        return tabs

    async def new_tab(self, url: str | None = None) -> dict[str, Any]:
        await self.start()
        if len(self._live_pages()) >= 10:
            raise BrowserAccessError("At most 10 tabs may be open")
        if url:
            await asyncio.wait_for(asyncio.to_thread(self._url_policy, url), timeout=5)
        page = await self._context.new_page()
        self._on_new_page(page)
        self._page = page
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        return {"index": self._live_pages().index(page), "url": page.url}

    async def switch_tab(self, index: int) -> dict[str, Any]:
        pages = self._live_pages()
        if not 0 <= index < len(pages):
            raise ValueError("No tab with that index")
        self._page = pages[index]
        await self._page.bring_to_front()
        return {"index": index, "url": self._page.url, "title": (await self._page.title())[:200]}

    async def close_tab(self, index: int) -> dict[str, Any]:
        pages = self._live_pages()
        if not 0 <= index < len(pages):
            raise ValueError("No tab with that index")
        if len(pages) == 1:
            raise BrowserAccessError("The last tab cannot be closed; close the browser instead")
        target = pages[index]
        await target.close()
        remaining = self._live_pages()
        if self._page is target or self._page.is_closed():
            self._page = remaining[min(index, len(remaining) - 1)]
        return {"closed": index, "active_url": self._page.url}

    # ---- screenshots --------------------------------------------------------------------------
    async def screenshot(self, *, full_page: bool = False) -> dict[str, Any]:
        page = self._require_page()
        if self.screenshot_dir is None:
            raise BrowserAccessError("Screenshots are not configured")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        name = datetime.now(UTC).strftime("shot-%Y%m%dT%H%M%S%fZ.png")
        target = self.screenshot_dir / name
        await page.screenshot(path=str(target), full_page=bool(full_page), timeout=10_000)
        size = target.stat().st_size
        return {"path": str(target), "bytes": size, "url": page.url, "verified": size > 0}

    # ---- approved, consequential actions (called through BrowserActions) -----------------
    async def _snapshot(self) -> dict[str, Any]:
        page = self._require_page()
        return {"url": page.url, "title": await page.title(),
                "digest": await self.current_state_digest(), "tabs": len(self._live_pages()),
                "page_ids": {id(p) for p in self._live_pages()}}

    async def _effect(self, before: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.2)  # let a popup register before looking for new tabs
        opened = [p for p in self._live_pages() if id(p) not in before["page_ids"]]
        if opened:
            self._page = opened[-1]
        await self._settle()
        new_tab = bool(opened)
        after = await self._snapshot()
        effect = {
            "url_changed": after["url"] != before["url"],
            "title_changed": after["title"] != before["title"],
            "content_changed": after["digest"] != before["digest"],
            "new_tab": new_tab or after["tabs"] > before["tabs"],
        }
        return {"url": after["url"], "title": after["title"][:300], "effect": effect,
                "verified": any(effect.values())}

    async def click(self, selector: str) -> dict[str, Any]:
        page = self._require_page()
        if not selector.strip() or len(selector) > 500:
            raise ValueError("A CSS selector of 1-500 characters is required")
        before = await self._snapshot()
        await page.locator(selector).first.click(timeout=10_000)
        return await self._effect(before)

    async def click_ref(self, ref: str) -> dict[str, Any]:
        locator = await self._ref(ref)
        before = await self._snapshot()
        await locator.click(timeout=10_000)
        result = await self._effect(before)
        if not result["verified"]:
            result["note"] = "The click was delivered but no page change was observed."
        return result

    async def upload_file(self, ref: str, file_path: Path) -> dict[str, Any]:
        locator = await self._ref(ref)
        if await locator.evaluate("e => e.tagName === 'INPUT' && e.type") != "file":
            raise BrowserAccessError("That control is not a file input")
        await locator.set_input_files(str(file_path), timeout=10_000)
        names = await locator.evaluate("e => Array.from(e.files).map(f => f.name)")
        return {"ref": ref, "attached": names, "verified": names == [file_path.name]}

    async def download_via(self, ref: str) -> dict[str, Any]:
        if self.download_dir is None:
            raise BrowserAccessError("Downloads are not configured")
        locator = await self._ref(ref)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        page = self._require_page()
        self._expecting_download = True
        try:
            async with page.expect_download(timeout=15_000) as info:
                await locator.click(timeout=10_000)
            download = await info.value
            suggested = download.suggested_filename or "download"
            safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(suggested).name)[:120].strip(" .") or "download"
            if Path(safe).suffix.lower() in _BLOCKED_DOWNLOAD_SUFFIXES:
                await download.cancel()
                raise BrowserAccessError(
                    f"Refusing to save {Path(safe).suffix} files (executable or script type)")
            target = self.download_dir / safe
            counter = 1
            while target.exists():
                target = self.download_dir / f"{Path(safe).stem}-{counter}{Path(safe).suffix}"
                counter += 1
            await download.save_as(str(target))
        finally:
            self._expecting_download = False
        size = target.stat().st_size
        if size > 100 * 1024 * 1024:
            target.unlink(missing_ok=True)
            raise BrowserAccessError("Download exceeds the 100 MB limit and was removed")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"path": str(target), "filename": target.name, "bytes": size, "sha256": digest,
                "verified": size >= 0 and target.is_file()}

    async def close(self) -> None:
        self._page = None
        self._pages = []
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
