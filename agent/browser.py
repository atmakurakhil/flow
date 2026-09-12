"""Isolated Playwright browser tools for Flow.

Each LangGraph thread gets its own incognito context. That makes a sequence of
browser calls useful (cookies and the current page survive), without letting a
different Slack or Teams conversation read that session.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from itertools import count
from typing import Any
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

_MAX_TEXT_LENGTH = 12_000
_MAX_ELEMENTS = 80
_DEFAULT_TIMEOUT_MS = 20_000


@dataclass
class BrowserSession:
    """The long-lived state for one conversation's browser."""

    playwright: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    references: dict[str, str]


class BrowserSessions:
    """Create and dispose thread-scoped Playwright contexts."""

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def page_for(self, thread_id: str) -> BrowserSession:
        async with self._lock:
            existing = self._sessions.get(thread_id)
            if existing is not None:
                return existing

            runner = await async_playwright().start()
            browser = await runner.chromium.launch(
                headless=os.environ.get("BROWSER_HEADLESS", "true").lower()
                not in {"0", "false", "no"},
                args=["--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1000},
                locale="en-US",
            )
            page = await context.new_page()
            page.set_default_timeout(_DEFAULT_TIMEOUT_MS)
            session = BrowserSession(
                playwright=runner,
                browser=browser,
                context=context,
                page=page,
                references={},
            )
            self._sessions[thread_id] = session
            print(f"[BROWSER] started session for thread {thread_id}")
            return session

    async def close(self, thread_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(thread_id, None)
        if session is None:
            return False
        await _close_session(session)
        print(f"[BROWSER] closed session for thread {thread_id}")
        return True

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(
            *(_close_session(session) for session in sessions),
            return_exceptions=True,
        )


async def _close_session(session: BrowserSession) -> None:
    try:
        await session.context.close()
    finally:
        try:
            await session.browser.close()
        finally:
            await session.playwright.stop()


browser_sessions = BrowserSessions()


def _thread_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable", {})
    thread_id = configurable.get("thread_id")
    # The Channel always sends a thread id. The fallback keeps direct graph
    # calls usable without exposing a caller-controlled session id to the LLM.
    return str(thread_id) if thread_id else "direct-run"


def _validated_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Browser URLs must be complete http:// or https:// URLs.")
    return url


def _selector(session: BrowserSession, target: str) -> str:
    if target.startswith("ref:"):
        reference = target.removeprefix("ref:")
        selector = session.references.get(reference)
        if selector is None:
            raise ValueError(
                f"Unknown browser reference {reference!r}. Run browser_snapshot first."
            )
        return selector
    return target


async def _snapshot(session: BrowserSession) -> dict[str, Any]:
    """Return readable page content plus stable targets for the next action."""
    page = session.page
    session.references.clear()
    reference_numbers = count(1)
    elements: list[dict[str, str]] = []
    locator = page.locator(
        "a, button, input, textarea, select, [role='button'], [contenteditable='true']"
    )
    total = min(await locator.count(), _MAX_ELEMENTS)

    for index in range(total):
        item = locator.nth(index)
        if not await item.is_visible():
            continue
        reference = str(next(reference_numbers))
        marker = f"flow-browser-{reference}"
        await item.evaluate(
            "(element, value) => element.setAttribute('data-flow-browser-ref', value)",
            marker,
        )
        selector = f'[data-flow-browser-ref="{marker}"]'
        session.references[reference] = selector
        text = (await item.inner_text()).strip()
        elements.append(
            {
                "ref": reference,
                "tag": await item.evaluate("element => element.tagName.toLowerCase()"),
                "text": text[:500],
                "aria_label": (await item.get_attribute("aria-label") or "")[:300],
                "placeholder": (await item.get_attribute("placeholder") or "")[:300],
                "type": (await item.get_attribute("type") or "")[:100],
                "href": (await item.get_attribute("href") or "")[:1_000],
            }
        )

    body_text = (await page.locator("body").inner_text()).strip()
    return {
        "url": page.url,
        "title": await page.title(),
        "text": body_text[:_MAX_TEXT_LENGTH],
        "interactive_elements": elements,
        "truncated": len(body_text) > _MAX_TEXT_LENGTH,
    }


async def _after_navigation(page: Page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=_DEFAULT_TIMEOUT_MS)
    except Exception:
        # Many useful apps continuously load or leave a long-lived connection
        # open. The page can still be inspected after a bounded wait.
        pass


@tool
async def browser_navigate(url: str, config: RunnableConfig) -> dict[str, Any]:
    """Open a public website in this conversation's isolated browser and inspect it."""
    session = await browser_sessions.page_for(_thread_id(config))
    await session.page.goto(_validated_url(url), wait_until="domcontentloaded")
    await _after_navigation(session.page)
    return await _snapshot(session)


@tool
async def browser_snapshot(config: RunnableConfig) -> dict[str, Any]:
    """Read the current browser page and return text plus numbered interactive targets."""
    session = await browser_sessions.page_for(_thread_id(config))
    return await _snapshot(session)


@tool
async def browser_click(target: str, config: RunnableConfig) -> dict[str, Any]:
    """Click a numbered `ref:<n>` from browser_snapshot, or a CSS selector, then inspect the page."""
    session = await browser_sessions.page_for(_thread_id(config))
    await session.page.locator(_selector(session, target)).click()
    await _after_navigation(session.page)
    return await _snapshot(session)


@tool
async def browser_fill(target: str, text: str, config: RunnableConfig) -> dict[str, Any]:
    """Replace the value of a numbered `ref:<n>` or CSS form-field selector."""
    session = await browser_sessions.page_for(_thread_id(config))
    await session.page.locator(_selector(session, target)).fill(text)
    return await _snapshot(session)


@tool
async def browser_press(
    key: str, config: RunnableConfig, target: str | None = None
) -> dict[str, Any]:
    """Press a key such as Enter, Tab, or Escape, optionally on a numbered target."""
    session = await browser_sessions.page_for(_thread_id(config))
    if target:
        await session.page.locator(_selector(session, target)).press(key)
    else:
        await session.page.keyboard.press(key)
    await _after_navigation(session.page)
    return await _snapshot(session)


@tool
async def browser_select_option(
    target: str, value: str, config: RunnableConfig
) -> dict[str, Any]:
    """Select an option value in a numbered `ref:<n>` or CSS select-field selector."""
    session = await browser_sessions.page_for(_thread_id(config))
    await session.page.locator(_selector(session, target)).select_option(value)
    return await _snapshot(session)


@tool
async def browser_close(config: RunnableConfig) -> str:
    """Close and erase this conversation's isolated browser session."""
    closed = await browser_sessions.close(_thread_id(config))
    return "Browser session closed." if closed else "No browser session was open."


browser_tools = [
    browser_navigate,
    browser_snapshot,
    browser_click,
    browser_fill,
    browser_press,
    browser_select_option,
    browser_close,
]
