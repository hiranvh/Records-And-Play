"""
playback.auth_service
---------------------
Authentication and login bootstrap helpers for playback sessions.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.sync_api import Page

from .models import StepType, WorkflowStep
from .navigation_service import NavigationService
from .screenshot_service import ScreenshotService


class AuthService:
    """Extracted authentication/login handling that operates on PlaybackSession state."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def _maybe_auto_login(self, page: Page) -> None:
        creds = self._cfg.credentials
        if not creds:
            return

        before_url = ""
        try:
            before_url = page.url or ""
        except Exception:
            before_url = ""

        try:
            user_el = page.locator(
                "input#UserName, input[name='UserName'], input[type='email']"
            ).first
            pwd_el = page.locator("input[type='password']").first
            if user_el.count() == 0 or pwd_el.count() == 0:
                return
            if not user_el.is_visible(timeout=2_000):
                return
        except Exception:
            return

        self._log("Login form detected — auto-filling credentials")
        try:
            user_el.fill(creds.get("username", ""), timeout=3_000)
            pwd_el.fill(creds.get("password", ""), timeout=3_000)
            clicked = self._click_login_button(page)
            if not clicked:
                raise RuntimeError("Login submit button not found")
            NavigationService.wait_ready(page, timeout_ms=self._NAV_TIMEOUT)
            self._wait_for_post_login_targets(page)
            self._ensure_login_success_or_raise(
                page,
                before_url=before_url,
                context="auto-login",
                capture_on_failure=True,
            )
            for s in self._steps:
                if s.is_credential_field or s.is_login_submit:
                    s.skip = True
        except Exception as exc:
            reason = str(exc)
            if "screenshot:" not in reason.lower():
                shot = self._capture_login_failure_screenshot(page, reason)
                if shot:
                    reason = f"{reason} (screenshot: {shot})"
            self._log(f"Auto-login error: {reason}", "WARNING")
            raise RuntimeError(f"Authentication failed (auto-login): {reason}") from exc

    def _wait_for_post_login_targets(self, page: Page, timeout_s: float = 12.0) -> bool:
        deadline = time.time() + max(timeout_s, 0.5)

        while time.time() < deadline:
            if self._has_post_login_target(page):
                return True
            time.sleep(0.25)
        return False

    def _ensure_login_success_or_raise(
        self,
        page: Page,
        before_url: str = "",
        context: str = "login",
        timeout_s: float = 12.0,
        capture_on_failure: bool = False,
    ) -> None:
        ok, reason = self._is_login_confirmed(page, before_url=before_url, timeout_s=timeout_s)
        if ok:
            return

        shot: Optional[str] = None
        if capture_on_failure:
            shot = self._capture_login_failure_screenshot(page, reason)

        detail = reason
        if shot:
            detail = f"{detail} (screenshot: {shot})"
        raise RuntimeError(f"Authentication failed ({context}): {detail}")

    def _is_login_confirmed(
        self,
        page: Page,
        before_url: str = "",
        timeout_s: float = 12.0,
    ) -> Tuple[bool, str]:
        deadline = time.time() + max(timeout_s, 0.5)
        before_norm = self._norm(before_url)
        selectors, url_tokens, cookie_tokens = self._configured_login_success_indicators()

        while time.time() < deadline:
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            current_norm = self._norm(current_url)

            if before_norm and current_norm and current_norm != before_norm:
                return True, ""

            if self._has_post_login_target(page):
                return True, ""

            if selectors and self._has_any_visible_selector(page, selectors):
                return True, ""

            if url_tokens and any(token in current_norm for token in url_tokens):
                return True, ""

            if self._has_session_signal(page, cookie_tokens):
                return True, ""

            time.sleep(0.25)

        return (
            False,
            "No redirect, post-login UI signal, session cookie, or configured success indicator detected",
        )

    def _has_post_login_target(self, page: Page) -> bool:
        selectors = (
            "a.divRedirectGrid.name",
            "a.divRedirectGrid",
            "a[data-redirecturl*='/Employees']",
            "#dashbourdId a.divRedirect",
            "#BtnAdd",
            "#BtnnAdd",
        )

        try:
            current_url = (page.url or "").lower()
        except Exception:
            current_url = ""

        if "/employees" in current_url or "/index/enrollment" in current_url or current_url.endswith("/index#/"):
            for selector in selectors:
                try:
                    loc = page.locator(selector).first
                    if loc.count() > 0 and loc.is_visible(timeout=500):
                        return True
                except Exception:
                    continue
        return False

    def _has_any_visible_selector(self, page: Page, selectors: List[str]) -> bool:
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    def _has_session_signal(self, page: Page, cookie_tokens: List[str]) -> bool:
        try:
            cookies = page.context.cookies()
        except Exception:
            return False

        if not cookies:
            return False

        tokens = [
            "session",
            "auth",
            "token",
            "sid",
            "jwt",
            "aspnet",
        ]
        tokens.extend(cookie_tokens)
        norm_tokens = [self._norm(token) for token in tokens if self._norm(token)]

        for cookie in cookies:
            name = self._norm(cookie.get("name") if isinstance(cookie, dict) else "")
            if not name:
                continue
            if any(token in name for token in norm_tokens):
                return True
        return False

    def _configured_login_success_indicators(self) -> Tuple[List[str], List[str], List[str]]:
        profile = self._cfg.execution_profile if isinstance(self._cfg.execution_profile, dict) else {}

        local_cfg: Dict[str, str] = {}
        try:
            local_cfg = self._load_playback_config_properties()
        except Exception:
            local_cfg = {}

        selectors = self._as_list(
            profile.get("login_success_selectors")
            or profile.get("login_success_selector")
            or profile.get("_login_success_selectors")
            or local_cfg.get("login_success_selectors")
            or local_cfg.get("login_success_selector")
        )
        url_tokens = self._as_list(
            profile.get("login_success_url_contains")
            or profile.get("login_success_url")
            or profile.get("_login_success_url_contains")
            or local_cfg.get("login_success_url_contains")
            or local_cfg.get("login_success_url")
        )
        cookie_tokens = self._as_list(
            profile.get("login_success_cookie")
            or profile.get("login_success_cookies")
            or profile.get("_login_success_cookie")
            or local_cfg.get("login_success_cookie")
            or local_cfg.get("login_success_cookies")
        )
        return selectors, [self._norm(token) for token in url_tokens], [self._norm(token) for token in cookie_tokens]

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            out = [str(v).strip() for v in value if str(v).strip()]
            return out

        text = str(value).strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]

    def _capture_login_failure_screenshot(self, page: Page, reason: str) -> Optional[str]:
        step = WorkflowStep(type=StepType.CLICK.value, label="Auto Login", text="Auto Login")
        return ScreenshotService.failure_screenshot(page, step, reason, self._log)

    def _click_login_button(self, page: Page) -> bool:
        for sel in (
            "#Login > button[type='submit']",
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Sign In')",
            "button:has-text('Log In')",
        ):
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=1_500):
                    btn.click(timeout=5_000, no_wait_after=True)
                    return True
            except Exception:
                continue
        return False

    def _password_coming(self, step: WorkflowStep) -> bool:
        for s in self._steps[step.index + 1: step.index + 6]:
            if s.is_password_field:
                return True
            if s.step_type in (StepType.CLICK, StepType.CLICK_LINK) and not s.is_login_submit:
                break
        return False

    def _login_click_ahead(self, step: WorkflowStep) -> bool:
        for s in self._steps[step.index + 1: step.index + 4]:
            if s.is_login_submit:
                return True
        return False

    def _mark_login_steps_done(self, after: WorkflowStep) -> None:
        for s in self._steps[after.index + 1:]:
            if s.is_login_submit or s.is_credential_field:
                s.skip = True
            else:
                break
