"""Isolated Playwright browser adapter with public-network guardrails."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


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


class BrowserController:
    """Own one short-lived, isolated browser context.

    It never attaches to the user's default browser profile. Downloads are
    disabled and all network requests are checked, including redirects.
    """

    def __init__(self, *, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def is_running(self) -> bool:
        return self._page is not None

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
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
            )
            self._context = await self._browser.new_context(
                accept_downloads=False,
                java_script_enabled=True,
            )
            self._page = await self._context.new_page()
            await self._page.route("**/*", self._guard_request)
        except Exception:
            await self.close()
            raise

    async def _guard_request(self, route) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(validate_public_url, route.request.url),
                timeout=5,
            )
        except Exception:
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def navigate(self, url: str) -> dict[str, object]:
        await self.start()
        await asyncio.wait_for(asyncio.to_thread(validate_public_url, url), timeout=5)
        response = await self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        title = await self._page.title()
        text = await self._page.locator("body").inner_text(timeout=5_000)
        links = await self._page.locator("a[href]").evaluate_all(
            """nodes => nodes.slice(0, 100).map(a => ({
                text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 180),
                href: a.href
            }))"""
        )
        return {
            "url": self._page.url,
            "title": title[:300],
            "status": response.status if response else None,
            "text": text[:30_000],
            "links": links,
        }

    async def click(self, selector: str) -> dict[str, str]:
        if not self.is_running:
            raise BrowserAccessError("Browser has not navigated to a page")
        if not selector.strip() or len(selector) > 500:
            raise ValueError("A CSS selector of 1-500 characters is required")
        await self._page.locator(selector).first.click(timeout=10_000)
        await self._page.wait_for_load_state("domcontentloaded", timeout=10_000)
        return {"url": self._page.url, "title": (await self._page.title())[:300]}

    async def close(self) -> None:
        self._page = None
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
