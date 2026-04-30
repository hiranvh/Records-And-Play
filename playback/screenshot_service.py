"""
playback.screenshot_service
---------------------------
Screenshot helpers for replay failures and session completion.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Callable, Optional

from playwright.sync_api import Page

from .models import WorkflowStep


class ScreenshotService:
    """Pure helpers for replay screenshot capture."""

    @staticmethod
    def failure_screenshot(
        page: Page,
        step: WorkflowStep,
        reason: str,
        log: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """
        Inject a JS red-banner overlay, take a full-page screenshot,
        then remove the overlay. Returns saved path or None.
        """
        try:
            shot_dir = os.path.join(os.getcwd(), "Screenshots")
            os.makedirs(shot_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            safe = re.sub(r"[^\w\-]+", "_", step.display_label)[:35]
            path = os.path.join(shot_dir, f"FAIL_{safe}_{ts}.png")

            lbl_js = step.display_label[:60].replace("\\", "\\\\").replace("`", "\\`").replace("'", "\\'")
            rsn_js = reason[:100].replace("\\", "\\\\").replace("`", "\\`").replace("'", "\\'")

            page.evaluate(
                f"""() => {{
                    try {{
                        const d = document.createElement('div');
                        d.id = '__pb_fail_banner__';
                        d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;'
                            + 'background:#c00010;color:#fff;padding:12px 20px;'
                            + 'font:bold 13px Arial,sans-serif;text-align:center;'
                            + 'border-bottom:4px solid #800000;box-shadow:0 2px 8px rgba(0,0,0,.5);';
                        d.textContent = 'MISSING / FAILED: {lbl_js}  |  {rsn_js}';
                        document.body && document.body.prepend(d);
                    }} catch(e) {{}}
                }}"""
            )

            page.screenshot(path=path, full_page=True)

            page.evaluate(
                "() => { const b = document.getElementById('__pb_fail_banner__'); "
                "if (b) b.remove(); }"
            )

            if log:
                log(f"  failure screenshot: {path}")
            return path
        except Exception:
            return None

    @staticmethod
    def final_screenshot(page: Page) -> Optional[str]:
        """Capture the final full-page screenshot at session end."""
        try:
            shot_dir = os.path.join(os.getcwd(), "Screenshots")
            os.makedirs(shot_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(shot_dir, f"playback_end_{ts}.png")
            page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            return None
