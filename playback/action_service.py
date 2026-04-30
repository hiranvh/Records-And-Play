"""
playback.action_service
-----------------------
Click and toggle action handlers for playback sessions.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from playwright.sync_api import Locator, Page

from .input_service import InputService
from .models import StepResult, WorkflowStep
from .navigation_service import NavigationService
from .screenshot_service import ScreenshotService


class ActionService:
    """Extracted click/toggle handling that operates on PlaybackSession state."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def _do_click(self, page: Page, step: WorkflowStep) -> StepResult:
        # jQuery UI datepicker day clicks — skip widget; date already filled directly
        if self._is_datepicker_day_click(step):
            return self._handle_datepicker_step(page, step)

        shortcut_result = self._try_dashboard_shortcut(page, step)
        if shortcut_result is not None:
            return shortcut_result

        nav_step = self._is_nav_step(step)
        check_employee_submission = self._is_employee_submission_step(page, step)
        before_url = page.url if check_employee_submission else ""

        if check_employee_submission:
            self._prepare_employee_form_for_submission(page)

        if self._is_chosen_step(step):
            self._open_chosen_dropdown(page, step)

        if self._is_plan_accordion_step(step):
            self._wait_for_plan_section_ready(page, timeout_s=20.0)
        if self._is_payment_step(step) or self._is_submit_enrollment_step(step):
            self._wait_for_payment_section_ready(page, timeout_s=12.0)

        el = self._find_with_retry(page, step)
        if not el:
            if self._is_plan_accordion_step(step) and self._click_plan_accordion_by_text(page, step):
                return StepResult(step.display_label, True, message="Plan accordion text fallback")
            if self._click_visible_action_by_text(page, step):
                return StepResult(step.display_label, True, message="Visible action text fallback")
            if self._cfg.group_name and self._click_group_row(page):
                return StepResult(step.display_label, True, message="Group row (auto-heal)")
            if self._is_chosen_step(step):
                target = step.text or step.label or step.value
                if self._click_chosen_option(page, target):
                    return StepResult(step.display_label, True,
                                      message=f"Chosen option: '{target}'")
            label = (step.text or step.id or step.selector or "?")[:50]
            reason = f"Click target not found: '{label}'"
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

        try:
            try:
                visible = el.is_visible(timeout=800)
            except Exception:
                visible = False
            if not visible:
                if self._is_plan_accordion_step(step) and self._click_plan_accordion_by_text(page, step):
                    return StepResult(step.display_label, True, message="Plan accordion text fallback")
                if self._click_visible_action_by_text(page, step):
                    return StepResult(step.display_label, True, message="Visible action text fallback")
                reason = f"Click target is hidden: '{(step.text or step.id or step.selector or '?')[:50]}'"
                shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

            if nav_step:
                readiness_problem = self._nav_click_preflight_problem(page, el)
                if readiness_problem:
                    reason = f"Click target not ready: {readiness_problem}"
                    shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                    return StepResult(step.display_label, False, message=reason, screenshot_path=shot)
                click_baseline = self._capture_nav_click_baseline(page, el)
            else:
                el.scroll_into_view_if_needed()
                click_baseline = {}

            el.click(
                timeout=5_000,
                no_wait_after=nav_step,
            )
            if nav_step:
                click_responded, click_signals = self._wait_for_nav_click_response(page, el, click_baseline)
                if not click_responded:
                    self._log("  click produced no application response; retrying after focus neutralization", "WARNING")
                    self._prepare_nav_click_retry(page)
                    retry_el = self._find_with_retry(page, step, retries=1) or el
                    retry_problem = self._nav_click_preflight_problem(page, retry_el)
                    if retry_problem:
                        self._log(f"  click retry skipped: {retry_problem}", "WARNING")
                    else:
                        retry_baseline = self._capture_nav_click_baseline(page, retry_el)
                        retry_el.click(timeout=5_000, no_wait_after=True)
                        click_responded, click_signals = self._wait_for_nav_click_response(
                            page,
                            retry_el,
                            retry_baseline,
                        )
                        el = retry_el

                if not click_responded:
                    reason = "Click executed but application did not respond."
                    self._log(
                        f"  no click response signals: {self._format_click_response_signals(click_signals)}",
                        "ERROR",
                    )
                    shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                    return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

            if check_employee_submission:
                NavigationService.wait_ready(page, timeout_ms=self._NAV_TIMEOUT)
                time.sleep(0.4)
                if not self._employee_submission_advanced(page, before_url):
                    errors = self._employee_form_validation_errors(page)
                    if errors:
                        reason = "Employee form validation blocked navigation: " + "; ".join(errors[:4])
                        shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                        return StepResult(step.display_label, False, message=reason, screenshot_path=shot)
                skipped_followups = self._mark_stale_employee_followup_steps(step)
                if skipped_followups:
                    self._log(
                        f"  skipping {skipped_followups} stale employee follow-up step(s) recorded after submission",
                        "WARNING",
                    )
            runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
            return StepResult(step.display_label, True, faker_value=runtime_value)
        except Exception as exc:
            reason = str(exc)
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

    def _nav_click_preflight_problem(self, page: Page, target: Locator) -> str:
        try:
            target.scroll_into_view_if_needed(timeout=1_500)
        except Exception as exc:
            return f"not scrollable into view ({exc})"

        try:
            if not target.is_visible(timeout=1_000):
                return "not visible"
        except Exception:
            return "visibility check failed"

        try:
            if not target.is_enabled(timeout=1_000):
                return "disabled"
        except Exception:
            return "enabled check failed"

        if not self._nav_click_in_viewport(target):
            try:
                target.scroll_into_view_if_needed(timeout=1_500)
            except Exception:
                pass
            if not self._nav_click_in_viewport(target):
                return "outside viewport"

        if not self._nav_click_is_stable(target):
            return "not stable"

        if not self._nav_click_uncovered(target):
            return "covered by another element"

        return ""

    @staticmethod
    def _nav_click_in_viewport(target: Locator) -> bool:
        try:
            return bool(
                target.evaluate(
                    """(element) => {
                        const rect = element.getBoundingClientRect();
                        const viewWidth = window.innerWidth || document.documentElement.clientWidth;
                        const viewHeight = window.innerHeight || document.documentElement.clientHeight;
                        return rect.width > 0 && rect.height > 0
                            && rect.bottom > 0 && rect.right > 0
                            && rect.top < viewHeight && rect.left < viewWidth;
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _nav_click_is_stable(target: Locator) -> bool:
        try:
            first_box = target.bounding_box()
            time.sleep(0.15)
            second_box = target.bounding_box()
        except Exception:
            return False

        if not first_box or not second_box:
            return False

        return all(
            abs(float(first_box.get(key, 0)) - float(second_box.get(key, 0))) <= 1.0
            for key in ("x", "y", "width", "height")
        )

    @staticmethod
    def _nav_click_uncovered(target: Locator) -> bool:
        try:
            return bool(
                target.evaluate(
                    """(element) => {
                        const rect = element.getBoundingClientRect();
                        const viewWidth = window.innerWidth || document.documentElement.clientWidth;
                        const viewHeight = window.innerHeight || document.documentElement.clientHeight;
                        const pointX = Math.min(Math.max(rect.left + rect.width / 2, 0), Math.max(viewWidth - 1, 0));
                        const pointY = Math.min(Math.max(rect.top + rect.height / 2, 0), Math.max(viewHeight - 1, 0));
                        const topElement = document.elementFromPoint(pointX, pointY);
                        if (!topElement) return false;
                        if (topElement === element || element.contains(topElement)) return true;
                        const label = topElement.closest ? topElement.closest('label') : null;
                        if (label && label.control === element) return true;
                        const clickable = topElement.closest
                            ? topElement.closest('button,a,input,select,textarea,[role="button"],[onclick]')
                            : null;
                        return clickable === element || (clickable && element.contains(clickable));
                    }"""
                )
            )
        except Exception:
            return False

    def _capture_nav_click_baseline(self, page: Page, target: Locator) -> Dict[str, Any]:
        source_token = self._mark_nav_click_source_container(target)
        return {
            "url": self._safe_page_url(page),
            "dom": InputService._dom_signature(page),
            "source_token": source_token,
            "source_container_visible": self._nav_source_container_visible(page, source_token),
            "enrollment_section_visible": self._nav_enrollment_section_visible(page),
            "success_message_visible": self._nav_success_message_visible(page),
            "validation_errors_visible": self._nav_validation_errors_visible(page),
        }

    @staticmethod
    def _mark_nav_click_source_container(target: Locator) -> str:
        token = f"replayclick{time.time_ns()}"
        try:
            marked = target.evaluate(
                """(element, token) => {
                    const source = element.closest(
                        'form, [role="form"], .form-horizontal, .wizard, .tab-pane, .panel, .card, .well, section, main'
                    ) || element;
                    source.setAttribute('data-replay-click-source', token);
                    return true;
                }""",
                token,
            )
            return token if marked else ""
        except Exception:
            return ""

    @staticmethod
    def _nav_source_container_visible(page: Page, source_token: str) -> bool:
        if not source_token:
            return False
        try:
            return bool(
                page.evaluate(
                    """(token) => {
                        const source = document.querySelector('[data-replay-click-source="' + token + '"]');
                        if (!source) return false;
                        const style = window.getComputedStyle(source);
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                        return !!(source.offsetWidth || source.offsetHeight || source.getClientRects().length);
                    }""",
                    source_token,
                )
            )
        except Exception:
            return False

    def _wait_for_nav_click_response(
        self,
        page: Page,
        target: Locator,
        baseline: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, bool]]:
        timeout_s = max(3.0, float(getattr(self, "_POST_NAV_FIELD_READY_TIMEOUT_S", 8.0)))
        deadline = time.time() + timeout_s
        saw_busy_overlay = False
        last_signals: Dict[str, bool] = {}

        while time.time() < deadline:
            overlays_cleared = self._loading_overlays_cleared(page)
            if not overlays_cleared:
                saw_busy_overlay = True

            signals = self._nav_click_response_signals(
                page=page,
                target=target,
                baseline=baseline,
                saw_busy_overlay=saw_busy_overlay,
                overlays_cleared=overlays_cleared,
            )
            last_signals = signals
            if any(signals.values()):
                return True, signals
            time.sleep(0.2)

        return False, last_signals

    def _nav_click_response_signals(
        self,
        page: Page,
        target: Locator,
        baseline: Dict[str, Any],
        saw_busy_overlay: bool,
        overlays_cleared: bool,
    ) -> Dict[str, bool]:
        current_url = self._safe_page_url(page)
        current_dom = InputService._dom_signature(page)
        source_token = str(baseline.get("source_token") or "")
        source_visible_before = bool(baseline.get("source_container_visible"))
        enrollment_visible = self._nav_enrollment_section_visible(page)
        success_visible = self._nav_success_message_visible(page)
        validation_visible = self._nav_validation_errors_visible(page)

        return {
            "url_changed": bool(baseline.get("url") and current_url and current_url != baseline.get("url")),
            "dom_changed": bool(baseline.get("dom") and current_dom and current_dom != baseline.get("dom")),
            "form_container_hidden": bool(
                source_visible_before and not self._nav_source_container_visible(page, source_token)
            ),
            "enrollment_section_visible": bool(enrollment_visible and not baseline.get("enrollment_section_visible")),
            "spinner_settled": bool(saw_busy_overlay and overlays_cleared),
            "button_disabled": self._nav_button_disabled(target),
            "success_message_visible": bool(success_visible and not baseline.get("success_message_visible")),
            "validation_errors_visible": bool(validation_visible and not baseline.get("validation_errors_visible")),
        }

    @staticmethod
    def _safe_page_url(page: Page) -> str:
        try:
            return page.url or ""
        except Exception:
            return ""

    def _nav_enrollment_section_visible(self, page: Page) -> bool:
        if self._plan_section_ready(page) or self._payment_context_visible(page):
            return True
        try:
            return bool(
                page.evaluate(
                    r"""() => {
                        const text = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        const visible = (element) => {
                            if (!element) return false;
                            const style = window.getComputedStyle(element);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
                        };
                        const bodyText = text(document.body ? document.body.innerText : '');
                        if (!/\b(enrollment|select plan|submit enrollment|payment method|decline coverage)\b/.test(bodyText)) {
                            return false;
                        }
                        return Array.from(document.querySelectorAll('form, section, main, .tab-pane, .panel, .card, div'))
                            .some((element) => visible(element) && /\b(enrollment|select plan|submit enrollment|payment method|decline coverage)\b/.test(text(element.innerText)));
                    }"""
                )
            )
        except Exception:
            return False

    def _nav_success_message_visible(self, page: Page) -> bool:
        try:
            return bool(
                self._success_toast_visible(page)
                or (self._confirmation_panel_visible(page) and not self._nav_validation_errors_visible(page))
            )
        except Exception:
            return False

    def _nav_validation_errors_visible(self, page: Page) -> bool:
        try:
            if self._employee_form_validation_errors(page):
                return True
        except Exception:
            pass
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const selectors = [
                            '.field-validation-error', '.text-danger', '.validation-summary-errors',
                            '.input-validation-error', '.is-invalid', '[aria-invalid="true"]'
                        ];
                        const visible = (element) => {
                            if (!element) return false;
                            const style = window.getComputedStyle(element);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
                        };
                        return selectors.some((selector) => Array.from(document.querySelectorAll(selector)).some((element) => {
                            if (!visible(element)) return false;
                            if (element.matches('[aria-invalid="true"], .input-validation-error, .is-invalid')) return true;
                            return String(element.innerText || element.textContent || '').trim().length > 0;
                        }));
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _nav_button_disabled(target: Locator) -> bool:
        try:
            if target.is_disabled(timeout=300):
                return True
        except Exception:
            pass
        try:
            return bool(
                target.evaluate(
                    """(element) => {
                        const ariaDisabled = String(element.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
                        return Boolean(element.disabled || element.hasAttribute('disabled') || ariaDisabled || element.classList.contains('disabled'));
                    }"""
                )
            )
        except Exception:
            return False

    def _prepare_nav_click_retry(self, page: Page) -> None:
        try:
            page.evaluate(
                """() => {
                    const activeElement = document.activeElement;
                    if (activeElement && activeElement !== document.body && activeElement !== document.documentElement) {
                        activeElement.dispatchEvent(new Event('input', { bubbles: true }));
                        activeElement.dispatchEvent(new Event('change', { bubbles: true }));
                        if (typeof activeElement.blur === 'function') activeElement.blur();
                    }
                }"""
            )
        except Exception:
            pass

        try:
            page.keyboard.press("Tab")
        except Exception:
            pass

        try:
            point = page.evaluate(
                """() => {
                    const interactive = 'a,button,input,select,textarea,label,[role="button"],[onclick]';
                    const width = window.innerWidth || document.documentElement.clientWidth || 0;
                    const height = window.innerHeight || document.documentElement.clientHeight || 0;
                    const points = [
                        [12, 12], [Math.max(width - 12, 1), 12],
                        [12, Math.max(height - 12, 1)], [Math.max(width - 12, 1), Math.max(height - 12, 1)],
                        [Math.max(width / 2, 1), 12], [Math.max(width / 2, 1), Math.max(height - 12, 1)]
                    ];
                    for (const point of points) {
                        const element = document.elementFromPoint(point[0], point[1]);
                        if (element && !element.closest(interactive)) {
                            return { x: point[0], y: point[1] };
                        }
                    }
                    return null;
                }"""
            )
            if isinstance(point, dict):
                page.mouse.click(float(point.get("x", 12)), float(point.get("y", 12)))
        except Exception:
            pass

    @staticmethod
    def _format_click_response_signals(signals: Dict[str, bool]) -> str:
        enabled = [name for name, state in signals.items() if state]
        return ", ".join(enabled) if enabled else "none"

    def _try_dashboard_shortcut(self, page: Page, step: WorkflowStep) -> Optional[StepResult]:
        blob = " ".join([
            step.text or "",
            step.label or "",
            step.selector or "",
            step.id or "",
            step.name or "",
        ]).lower()

        if "employee administration" not in blob:
            return None

        current_url = (page.url or "").lower()
        if current_url.endswith("/employees") or "/group/searchemployee" in current_url or "/employees" in current_url:
            return StepResult(
                step.display_label,
                True,
                skipped=True,
                message="Already on Employee Administration page",
            )

        selectors = (
            "a[data-redirecturl*='/Employees']",
            "#dashbourdId a.divRedirect",
            "#dashbourdId a[href*='/Employees']",
            "a[href*='/Employees']",
        )

        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0 or not loc.is_visible(timeout=1_000):
                    continue
                loc.scroll_into_view_if_needed()
                loc.click(timeout=5_000, no_wait_after=False, force=True)
                return StepResult(step.display_label, True, message=f"Shortcut click: {selector}")
            except Exception:
                continue

        return None

    def _do_toggle(self, page: Page, step: WorkflowStep) -> StepResult:
        want = (step.value or "").lower() in ("true", "yes", "1", "on")
        itype = (step.input_type or "").lower()

        if self._is_product_plan_step(step):
            self._wait_for_product_choice_ready(page, step, timeout_s=20.0)
        if self._is_payment_step(step):
            self._wait_for_payment_section_ready(page, timeout_s=12.0)

        if itype == "radio":
            el = self._find_radio_option(page, step)
            if not el:
                if self._is_product_plan_step(step) and self._click_dynamic_plan_choice(page, step):
                    runtime_value = self._runtime_action_value(page, step) if self._is_runtime_action_data_step(step) else ""
                    return StepResult(step.display_label, True, message="Dynamic plan choice fallback", faker_value=runtime_value)
                reason = f"Toggle not found — id='{step.id or '?'}', name='{step.name or '?'}'"
                shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

            try:
                el.check(timeout=3_000, force=True)
                self._post_toggle_stabilize(page, step, True)
                runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
                return StepResult(step.display_label, True, faker_value=runtime_value)
            except Exception as exc:
                try:
                    self._set_checked_via_js(el, True)
                    self._post_toggle_stabilize(page, step, True)
                    runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
                    return StepResult(step.display_label, True, message="Toggled via JS", faker_value=runtime_value)
                except Exception:
                    reason = str(exc)
                    shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                    return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

        el = self._find_with_retry(page, step)
        if not el:
            if self._is_product_decline_step(step) and not want and self._plan_section_ready(page):
                return StepResult(
                    step.display_label,
                    True,
                    skipped=True,
                    message="Decline control absent; treated as not declined",
                )
            reason = f"Toggle not found — id='{step.id or '?'}'"
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason, screenshot_path=shot)

        try:
            if itype == "checkbox":
                try:
                    if el.is_checked() == want:
                        self._post_toggle_stabilize(page, step, want)
                        runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
                        return StepResult(step.display_label, True, skipped=True,
                                          message="Already in desired state", faker_value=runtime_value)
                except Exception:
                    pass
                try:
                    if want:
                        el.check(timeout=3_000, force=True)
                    else:
                        el.uncheck(timeout=3_000, force=True)
                    self._post_toggle_stabilize(page, step, want)
                    runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
                    return StepResult(step.display_label, True, faker_value=runtime_value)
                except Exception:
                    self._set_checked_via_js(el, want)
                    self._post_toggle_stabilize(page, step, want)
                    runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
                    return StepResult(step.display_label, True, message="Toggled via JS", faker_value=runtime_value)

            el.click(timeout=3_000)
            self._post_toggle_stabilize(page, step, want)
            runtime_value = self._runtime_action_value(page, step, el) if self._is_runtime_action_data_step(step) else ""
            return StepResult(step.display_label, True, faker_value=runtime_value)
        except Exception as exc:
            reason = str(exc)
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason, screenshot_path=shot)
