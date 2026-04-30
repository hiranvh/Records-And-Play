"""
playback.input_service
----------------------
Text input fill and masked input helpers for playback sessions.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any

from playwright.sync_api import Locator, Page

from .models import StepResult, WorkflowStep
from .navigation_service import NavigationService
from .screenshot_service import ScreenshotService


class InputService:
    """Extracted text-input handling that operates on PlaybackSession state."""

    _APP_REFRESH_TIMEOUT_MIN_S = 20.0
    _APP_REFRESH_TIMEOUT_MAX_S = 30.0

    def __init__(self, session: Any) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def _fill_input(self, page: Page, step: WorkflowStep) -> StepResult:
        # Credentials use real values; all other inputs use Faker
        if step.is_credential_field:
            value = (
                self._cfg.credentials.get("password")
                if step.is_password_field
                else self._cfg.credentials.get("username")
            ) or step.value or ""
        else:
            value = self._faker.generate(step) or step.value or ""

        if not value:
            return StepResult(step.display_label, True, skipped=True, message="No value resolved")

        el = self._find_with_retry(page, step)
        if not el:
            reason = (
                f"Element not found — id='{step.id or '?'}', "
                f"name='{step.name or '?'}', label='{step.label or '?'}'"
            )
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason,
                              faker_value=value, screenshot_path=shot)

        try:
            fill_target = self._resolve_fill_locator(page, step, el)
            fill_target.scroll_into_view_if_needed()
            refresh_trigger = self._refresh_trigger_kind(step)
            self._set_text_input_value(fill_target, str(value))
            actual_value = self._read_input_value(fill_target)
            if not self._input_value_matches(step, actual_value, str(value)) and fill_target != el:
                actual_value = self._read_input_value(el)
            if not self._input_value_matches(step, actual_value, str(value)):
                raise ValueError(
                    f"Entered '{value}' but field now contains '{actual_value}'"
                )
            refresh_activity_seen = False
            if refresh_trigger:
                refresh_activity_seen = self._commit_refresh_field(
                    page=page,
                    el=fill_target,
                    step=step,
                    trigger=refresh_trigger,
                    desired_value=str(value),
                )
            NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)

            refresh_note = ""
            refresh_shot = None
            if refresh_trigger:
                refreshed, blocked_next_step, refresh_shot = self._wait_for_application_refresh(
                    page=page,
                    step=step,
                    trigger=refresh_trigger,
                    activity_seen=refresh_activity_seen,
                )
                if not refreshed:
                    if blocked_next_step:
                        raise TimeoutError(
                            "Application refresh timeout and next recorded step is not actionable"
                        )
                    refresh_note = " | Refresh timeout, continuing cautiously."
                elif refresh_trigger == "effective":
                    self._suppress_stale_steps_after_context_change(
                        page=page,
                        current_step=step,
                        baseline_snapshot=self._scan_current_page_fields(page),
                    )

            if step.is_password_field:
                if self._session._pending_login_click or not self._login_click_ahead(step):
                    try:
                        before_url = page.url or ""
                    except Exception:
                        before_url = ""

                    clicked = self._click_login_button(page)
                    if not clicked:
                        raise RuntimeError("Authentication failed (password submit): Login submit button not found")

                    NavigationService.wait_ready(page, timeout_ms=self._NAV_TIMEOUT)
                    self._ensure_login_success_or_raise(
                        page,
                        before_url=before_url,
                        context="password submit",
                        timeout_s=10.0,
                    )
                    self._session._pending_login_click = False
                    self._mark_login_steps_done(step)

            log_val = "[HIDDEN]" if step.is_password_field else value
            return StepResult(
                step.display_label, True, message=f"= '{log_val}'{refresh_note}",
                faker_value="" if step.is_credential_field else value,
                screenshot_path=refresh_shot,
            )
        except Exception as exc:
            reason = str(exc)
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason,
                              faker_value=value, screenshot_path=shot)

    def _refresh_trigger_kind(self, step: WorkflowStep) -> str:
        blob = self._refresh_field_blob(step)
        if not blob:
            return ""

        if any(
            token in blob
            for token in (
                "effectivedate",
                "effective_date",
                "coverageeffectivedate",
                "coverage_effective_date",
                "enrollmenteffectivedate",
                "enrollment_effective_date",
                "effective date",
                "coverage effective date",
                "enrollment effective date",
            )
        ):
            return "effective"

        zip_extension_tokens = ("zipext", "zip_extension", "zipextension", "zipcodeextension", "postalcodeextension")
        if any(token in blob for token in zip_extension_tokens):
            return ""

        if any(
            token in blob
            for token in (
                "zip code",
                "zipcode",
                "zip_code",
                "postal code",
                "postalcode",
                "postal_code",
                " zip ",
            )
        ):
            return "zip"

        return ""

    @staticmethod
    def _refresh_field_blob(step: WorkflowStep) -> str:
        raw = " ".join(
            [
                step.id or "",
                step.name or "",
                step.label or "",
                step.placeholder or "",
                step.aria_label or "",
                step.text or "",
            ]
        )
        normalized = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
        return f" {normalized} " if normalized else ""

    def _commit_refresh_field(
        self,
        page: Page,
        el: Locator,
        step: WorkflowStep,
        trigger: str,
        desired_value: str,
    ) -> bool:
        before_signature = self._dom_signature(page)

        try:
            el.evaluate(
                """(node) => {
                    node.dispatchEvent(new Event('input', { bubbles: true }));
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                }"""
            )
        except Exception:
            pass

        try:
            el.press("Tab", timeout=1_200)
        except Exception:
            try:
                el.evaluate("(node) => node.blur && node.blur()")
            except Exception:
                pass

        if self._field_has_focus(el):
            try:
                el.evaluate(
                    """(node) => {
                        node.dispatchEvent(new Event('blur', { bubbles: false }));
                        node.blur && node.blur();
                    }"""
                )
            except Exception:
                pass

        actual_value = self._read_input_value(el)
        if not self._input_value_matches(step, actual_value, desired_value):
            raise ValueError(
                f"Field did not commit '{desired_value}' after blur; current value is '{actual_value}'"
            )

        return self._wait_for_refresh_activity(page, before_signature, trigger)

    @staticmethod
    def _field_has_focus(el: Locator) -> bool:
        try:
            return bool(el.evaluate("(node) => document.activeElement === node"))
        except Exception:
            return False

    def _wait_for_refresh_activity(self, page: Page, before_signature: str, trigger: str) -> bool:
        deadline = time.time() + (4.0 if trigger == "effective" else 2.0)
        while time.time() < deadline:
            if not self._loading_overlays_cleared(page):
                return True
            if not self._ajax_settled(page):
                return True
            signature = self._dom_signature(page)
            if before_signature and signature and signature != before_signature:
                return True
            if trigger == "effective" and self._effective_controls_loaded(page):
                return True
            time.sleep(0.15)
        return False

    def _wait_for_application_refresh(
        self,
        page: Page,
        step: WorkflowStep,
        trigger: str,
        activity_seen: bool = False,
    ) -> tuple[bool, bool, Any]:
        timeout_s = self._application_refresh_timeout_s()
        label = "Effective Date" if trigger == "effective" else "Zip Code"
        self._log(f"Waiting for application refresh after {label}...")

        deadline = time.time() + timeout_s
        started_at = time.time()
        initial_dom_signature = self._dom_signature(page)
        last_dom_signature = ""
        stable_hits = 0
        observed_activity = bool(activity_seen)
        warned_no_activity = False

        while time.time() < deadline:
            spinner_clear = self._loading_overlays_cleared(page)
            ajax_settled = self._ajax_settled(page)
            dependent_ready = self._dependent_controls_enabled(page)
            domain_ready = (
                self._zip_fields_populated(page)
                if trigger == "zip"
                else self._effective_controls_loaded(page)
            )
            next_ready = self._next_recorded_step_actionable(page, step)

            dom_signature = self._dom_signature(page)
            if dom_signature and dom_signature == last_dom_signature:
                stable_hits += 1
            else:
                stable_hits = 0
                last_dom_signature = dom_signature
            dom_stable = stable_hits >= 2

            if not spinner_clear or not ajax_settled:
                observed_activity = True
            if initial_dom_signature and dom_signature and dom_signature != initial_dom_signature:
                observed_activity = True

            if trigger == "effective":
                ready = bool(domain_ready and spinner_clear and (ajax_settled or dom_stable))
                if (
                    not ready
                    and not observed_activity
                    and not warned_no_activity
                    and time.time() - started_at >= 2.0
                ):
                    self._log(
                        "No plan refresh activity observed after Effective Date commit; waiting for plan controls.",
                        "WARNING",
                    )
                    warned_no_activity = True
            else:
                ready = bool(
                    domain_ready
                    or next_ready
                    or (dependent_ready and spinner_clear and (ajax_settled or dom_stable))
                )
            if ready:
                return True, False, None

            time.sleep(0.25)

        blocked_next_step = (
            not self._effective_controls_loaded(page)
            if trigger == "effective"
            else not self._next_recorded_step_actionable(page, step)
        )
        if blocked_next_step:
            self._log("Refresh timeout and next recorded step remains blocked.", "WARNING")
            return False, True, None

        shot = self._refresh_timeout_screenshot(page, step, trigger)
        self._log("Refresh timeout, continuing cautiously.", "WARNING")
        return False, False, shot

    def _application_refresh_timeout_s(self) -> float:
        raw_value = None
        try:
            profile = self._cfg.execution_profile if isinstance(self._cfg.execution_profile, dict) else {}
            raw_value = (
                profile.get("app_refresh_wait_timeout_s")
                or profile.get("application_refresh_wait_timeout_s")
                or os.environ.get("REPLAY_APP_REFRESH_TIMEOUT_S")
            )
        except Exception:
            raw_value = None

        try:
            value = float(raw_value)
        except Exception:
            value = 25.0

        return max(self._APP_REFRESH_TIMEOUT_MIN_S, min(self._APP_REFRESH_TIMEOUT_MAX_S, value))

    @staticmethod
    def _ajax_settled(page: Page) -> bool:
        try:
            page.wait_for_load_state("networkidle", timeout=900)
            return True
        except Exception:
            pass

        try:
            return bool(
                page.evaluate(
                    r"""() => {
                        if (!window.jQuery) return true;
                        return Number(window.jQuery.active || 0) === 0;
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _loading_overlays_cleared(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const selectors = [
                            '.loading', '.loading-overlay', '.loader', '.spinner', '.busy',
                            '.blockUI', '.k-loading-mask', '.ui-widget-overlay',
                            '[aria-busy="true"]', '.progress', '.overlay'
                        ];
                        const visible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) {
                                return false;
                            }
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };
                        return selectors.every((selector) => {
                            const nodes = Array.from(document.querySelectorAll(selector));
                            return nodes.every((node) => !visible(node));
                        });
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _dependent_controls_enabled(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const controls = Array.from(document.querySelectorAll('select, input, button'));
                        const target = controls.filter((el) => {
                            const id = String(el.id || '').toLowerCase();
                            const name = String(el.name || '').toLowerCase();
                            const blob = `${id} ${name}`;
                            return /(county|city|state|plan|enroll|coverage|benefit|product)/.test(blob);
                        });
                        if (!target.length) return false;
                        return target.some((el) => {
                            if (el.disabled) return false;
                            if (el.tagName === 'SELECT') {
                                return (el.options && el.options.length > 1) || String(el.value || '').trim() !== '';
                            }
                            return true;
                        });
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _zip_fields_populated(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const fields = Array.from(document.querySelectorAll('input, select'));
                        const target = fields.filter((el) => {
                            const id = String(el.id || '').toLowerCase();
                            const name = String(el.name || '').toLowerCase();
                            const blob = `${id} ${name}`;
                            return /(county|city|state)/.test(blob);
                        });
                        if (!target.length) return false;
                        return target.some((el) => {
                            if (el.tagName === 'SELECT') {
                                if ((el.options && el.options.length > 1) || String(el.value || '').trim() !== '') return true;
                                return false;
                            }
                            return String((el.value || '')).trim() !== '';
                        });
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _effective_controls_loaded(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    r"""() => {
                        const txt = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        const visible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };

                        const bodyText = txt(document.body ? document.body.innerText : '');
                        if (/\b(select plan|medical\s+\d+\s+plans?|dental\s+\d+\s+plans?|vision\s+\d+\s+plans?)\b/.test(bodyText)) {
                            return true;
                        }

                        const planLinks = Array.from(document.querySelectorAll('a.planAccordian, a, button, [role="button"]'));
                        if (planLinks.some((el) => visible(el) && /\b(plans?|select plan)\b/.test(txt(el.innerText || el.textContent || el.value)))) {
                            return true;
                        }

                        const productControls = Array.from(document.querySelectorAll('input, label, button, a, div, section'));
                        return productControls.some((el) => {
                            const id = txt(el.id);
                            const name = txt(el.name);
                            const text = txt(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'));
                            const productIdentity = /productlst|selectedlineid|isdeclinedind/.test(`${id} ${name}`);
                            const productText = /\b(select plan|decline coverage|waive)\b/.test(text);
                            return (productIdentity || productText) && visible(el);
                        });
                    }"""
                )
            )
        except Exception:
            return False

    def _next_recorded_step_actionable(self, page: Page, current_step: WorkflowStep) -> bool:
        next_step = self._next_pending_step(current_step)
        if next_step is None:
            return True
        actionable, _ = self._is_step_actionable(page, next_step)
        return actionable

    def _next_pending_step(self, current_step: WorkflowStep) -> WorkflowStep | None:
        start = int(current_step.index) + 1
        for idx in range(max(0, start), len(self._steps)):
            candidate = self._steps[idx]
            if candidate.skip or candidate.executed:
                continue
            return candidate
        return None

    @staticmethod
    def _dom_signature(page: Page) -> str:
        try:
            return str(
                page.evaluate(
                    """() => {
                        const root = document.body;
                        if (!root) return '';
                        const textLen = String(root.innerText || '').length;
                        return `${root.childElementCount}:${textLen}`;
                    }"""
                )
                or ""
            )
        except Exception:
            return ""

    def _refresh_timeout_screenshot(self, page: Page, step: WorkflowStep, trigger: str) -> Any:
        try:
            shot_dir = os.path.join(os.getcwd(), "Screenshots")
            os.makedirs(shot_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            safe = re.sub(r"[^\w\-]+", "_", step.display_label or trigger)[:35]
            path = os.path.join(shot_dir, f"WARN_REFRESH_{safe}_{ts}.png")
            page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            return None

    def _resolve_fill_locator(self, page: Page, step: WorkflowStep, el: Locator) -> Locator:
        if step.id:
            for sel in (f"[id='{step.id}_mask']",):
                try:
                    mask = page.locator(sel).first
                    if mask.count() > 0 and mask.is_visible(timeout=300):
                        return mask
                except Exception:
                    continue
        if step.name:
            for sel in (f"[name='{step.name}_mask']",):
                try:
                    mask = page.locator(sel).first
                    if mask.count() > 0 and mask.is_visible(timeout=300):
                        return mask
                except Exception:
                    continue
        return el

    @staticmethod
    def _set_text_input_value(el: Locator, value: str) -> None:
        try:
            el.click(timeout=3_000, force=True)
        except Exception:
            pass

        try:
            el.press("Control+A", timeout=1_500)
            el.press("Delete", timeout=1_500)
        except Exception:
            try:
                el.fill("", timeout=1_500)
            except Exception:
                pass

        try:
            el.type(value, delay=35, timeout=5_000)
        except Exception:
            el.fill(value, timeout=5_000)

    @staticmethod
    def _read_input_value(el: Locator) -> str:
        try:
            return el.input_value(timeout=1_500)
        except Exception:
            try:
                return str(el.get_attribute("value") or "")
            except Exception:
                return ""

    @staticmethod
    def _input_value_matches(step: WorkflowStep, actual: str, desired: str) -> bool:
        actual = (actual or "").strip()
        desired = (desired or "").strip()
        if actual == desired:
            return True

        blob = " ".join([step.id, step.name, step.label, step.placeholder]).lower()
        if any(token in blob for token in ("phone", "ssn", "zip", "date", "dob")):
            digits_actual = re.sub(r"\D+", "", actual)
            digits_desired = re.sub(r"\D+", "", desired)
            return bool(digits_actual) and digits_actual == digits_desired

        # Tolerate HTML maxlength truncation: field cut desired to len(actual)
        if actual and desired.lower().startswith(actual.lower()) and 1 <= len(actual) < len(desired):
            return True

        return actual.lower() == desired.lower()
