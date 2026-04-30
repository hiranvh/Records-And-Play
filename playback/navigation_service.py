"""
playback.navigation_service
---------------------------
Navigation and wait helpers for playback sessions.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from playwright.sync_api import Page


class NavigationService:
    """Pure helpers for navigation and readiness waits."""

    @staticmethod
    def navigate(
        page: Page,
        url: str,
        log: Optional[Callable[..., None]] = None,
    ) -> None:
        try:
            page.goto(url, timeout=60_000, wait_until="domcontentloaded")
        except Exception as exc:
            if log:
                try:
                    log(f"Navigation warning: {exc}", "WARNING")
                except TypeError:
                    log(f"Navigation warning: {exc}")

    @staticmethod
    def wait_ready(page: Page, timeout_ms: int) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            time.sleep(2)

    @staticmethod
    def wait_ajax(page: Page, timeout_ms: int) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass
