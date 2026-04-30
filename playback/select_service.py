"""
playback.select_service
-----------------------
Dropdown/select handling helpers for playback sessions.
"""
from __future__ import annotations

import time
from typing import Any, List, Optional, Tuple

from playwright.sync_api import Locator, Page

from .models import StepResult, WorkflowStep
from .navigation_service import NavigationService
from .screenshot_service import ScreenshotService


class SelectService:
    """Extracted select/dropdown handling that operates on PlaybackSession state."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def _fill_select(self, page: Page, step: WorkflowStep) -> StepResult:
        # jQuery UI datepicker year/month selects — skip widget; fill input directly
        if self._is_datepicker_select(step):
            return self._handle_datepicker_step(page, step)

        # Use recorded value — valid options cannot be predicted at design time
        value = step.value or ""
        if not value:
            return StepResult(step.display_label, True, skipped=True,
                              message="No recorded value for select")

        has_chosen_widget = self._has_chosen_widget(page, step)
        el = self._find_with_retry(
            page,
            step,
            allow_hidden_custom_widget=has_chosen_widget,
        )
        if not el:
            if self._chosen_select(page, step, value):
                NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                return StepResult(step.display_label, True,
                                  message=f"= '{value}' (Chosen)",
                                  faker_value=f"recorded:{value}")
            reason = f"Select element not found — id='{step.id or '?'}'"
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason,
                              faker_value=f"recorded:{value}", screenshot_path=shot)

        try:
            # For address-autofill selects, ensure the ZIP is filled first so
            # the AJAX-driven county/city/state options are populated.
            if self._is_address_autofill_select(step):
                self._ensure_zip_filled(page, step)
            try:
                is_visible = el.is_visible(timeout=300)
            except Exception:
                is_visible = False

            if not is_visible:
                target = step.text or value
                if has_chosen_widget and self._chosen_select(page, step, target):
                    NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                    time.sleep(0.2)
                    current_value, current_text = self._read_select_state(el)
                    display = current_text or current_value or target
                    return StepResult(
                        step.display_label,
                        True,
                        message=f"= '{display}'",
                        faker_value=f"recorded:{display}",
                    )

                reason = (
                    f"Select control is hidden and not actionable - id='{step.id or '?'}'"
                )
                shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                return StepResult(
                    step.display_label,
                    False,
                    message=reason,
                    faker_value=f"recorded:{value}",
                    screenshot_path=shot,
                )

            el.scroll_into_view_if_needed()
            self._wait_for_select_options(el, step, value, timeout_s=5.5)
            if self._is_address_autofill_select(step):
                NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                time.sleep(0.6)
                current_value, current_text = self._read_select_state(el)
                if self._should_keep_existing_select(step, current_value, current_text):
                    display = current_text or current_value
                    return StepResult(
                        step.display_label,
                        True,
                        skipped=True,
                        message=f"Already populated with '{display}'",
                        faker_value=f"recorded:{display}",
                    )
                mapped_value = self._mapped_address_select_value(page, step)
                if mapped_value:
                    value = mapped_value
                    self._wait_for_select_options(el, step, value, timeout_s=5.5)
            try:
                el.select_option(value=value, timeout=3_000)
            except Exception:
                try:
                    el.select_option(label=value, timeout=3_000)
                except Exception:
                    if self._chosen_select(page, step, value):
                        NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                        return StepResult(step.display_label, True,
                                          message=f"= '{value}' (Chosen)",
                                          faker_value=f"recorded:{value}")
                    reason = f"Could not select '{value}'"
                    shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                    return StepResult(step.display_label, False, message=reason,
                                      faker_value=f"recorded:{value}", screenshot_path=shot)
            NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
            time.sleep(0.35)
            current_value, current_text = self._read_select_state(el)
            if not self._select_state_matches(step, current_value, current_text, value):
                target = step.text or value
                if has_chosen_widget and self._chosen_select(page, step, target):
                    NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                    time.sleep(0.2)
                    current_value, current_text = self._read_select_state(el)

            if not self._select_state_matches(step, current_value, current_text, value):
                self._set_select_via_js(el, value, step.text or value)
                NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                time.sleep(0.2)
                current_value, current_text = self._read_select_state(el)

            if not self._select_state_matches(step, current_value, current_text, value):
                reason = f"Could not select '{value}'"
                shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                return StepResult(step.display_label, False, message=reason,
                                  faker_value=f"recorded:{value}", screenshot_path=shot)

            display = current_text or current_value or value
            return StepResult(step.display_label, True,
                              message=f"= '{display}'", faker_value=f"recorded:{display}")
        except Exception as exc:
            reason = str(exc)
            shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
            return StepResult(step.display_label, False, message=reason,
                              faker_value=f"recorded:{value}", screenshot_path=shot)

    @staticmethod
    def _is_address_autofill_select(step: WorkflowStep) -> bool:
        blob = " ".join([step.id, step.name]).lower()
        return "address" in blob and any(token in blob for token in ("county", "city", "state"))

    def _mapped_address_select_value(self, page: Page, step: WorkflowStep) -> str:
        from core.constants import ZIP_LOCATION_DATA

        blob = " ".join([step.id, step.name]).lower()
        if "county" in blob:
            field = "county"
        elif "city" in blob:
            field = "city"
        elif "state" in blob:
            field = "state"
        else:
            return ""

        selectors: List[str] = []
        if "homeaddress" in blob:
            selectors = [
                "#EmployeeVM_Person_HomeAddress_ZipCode",
                "[name='EmployeeVM.Person.HomeAddress.ZipCode']",
            ]
        elif "mailingaddress" in blob:
            selectors = [
                "#EmployeeVM_Person_MailingAddress_ZipCode",
                "#EmployeeVM_Person_MailingAddress_NonUSZipCode",
                "[name='EmployeeVM.Person.MailingAddress.ZipCode']",
                "[name='EmployeeVM.Person.MailingAddress.NonUSZipCode']",
            ]

        for sel in selectors:
            try:
                zip_loc = page.locator(sel).first
                if zip_loc.count() == 0:
                    continue
                zip_code = (zip_loc.input_value() or "").strip()
                if zip_code and zip_code in ZIP_LOCATION_DATA:
                    return str(ZIP_LOCATION_DATA[zip_code].get(field, "") or "")
            except Exception:
                continue
        return ""

    def _wait_for_select_options(
        self,
        el: Locator,
        step: WorkflowStep,
        desired_value: str,
        timeout_s: float = 5.0,
    ) -> None:
        deadline = time.time() + max(timeout_s, 0.1)
        desired = {
            (desired_value or "").strip().lower(),
            (step.text or "").strip().lower(),
            (step.label or "").strip().lower(),
        }
        desired.discard("")

        initial_options = self._read_select_options(el)
        saw_non_empty = any((opt[0] or opt[1]).strip() for opt in initial_options)

        while time.time() < deadline:
            options = self._read_select_options(el)
            non_empty = [(val, txt) for val, txt in options if (val or txt).strip()]
            if desired and any(val.lower() in desired or txt.lower() in desired for val, txt in non_empty):
                return
            if non_empty and (not desired or len(non_empty) > 1 or not saw_non_empty):
                return
            time.sleep(0.2)

    def _has_chosen_widget(self, page: Page, step: WorkflowStep) -> bool:
        if not step.id:
            return False
        for sel in (
            f"#{step.id}_chzn",
            f"#{step.id}_chosen",
            f"[id='{step.id}_chzn']",
            f"[id='{step.id}_chosen']",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _read_select_options(el: Locator) -> List[Tuple[str, str]]:
        try:
            rows = el.evaluate(
                """(node) => Array.from(node.options || []).map(opt => [
                    String(opt.value || '').trim(),
                    String(opt.text || '').trim(),
                ])"""
            ) or []
            out: List[Tuple[str, str]] = []
            for row in rows:
                if isinstance(row, list) and len(row) >= 2:
                    out.append((str(row[0] or ""), str(row[1] or "")))
            return out
        except Exception:
            return []

    @staticmethod
    def _set_select_via_js(el: Locator, desired_value: str, desired_text: str = "") -> None:
        el.evaluate(
            """(node, payload) => {
                const desiredValue = String((payload && payload.value) || '').trim();
                const desiredText = String((payload && payload.text) || '').trim().toLowerCase();
                const options = Array.from(node.options || []);
                let match = null;
                if (desiredValue) {
                    match = options.find(opt => String(opt.value || '').trim() === desiredValue) || null;
                }
                if (!match && desiredText) {
                    match = options.find(opt => String(opt.text || '').trim().toLowerCase() === desiredText) || null;
                }
                if (!match) {
                    return;
                }
                node.value = String(match.value || '').trim();
                for (const opt of options) {
                    opt.selected = opt === match;
                }
                node.dispatchEvent(new Event('input', { bubbles: true }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            {"value": desired_value, "text": desired_text},
        )

    @staticmethod
    def _read_select_state(el: Locator) -> Tuple[str, str]:
        try:
            data = el.evaluate(
                """(node) => ({
                    value: node.value || '',
                    text: node.selectedOptions && node.selectedOptions[0]
                        ? (node.selectedOptions[0].text || '').trim()
                        : ''
                })"""
            ) or {}
            return str(data.get("value", "")), str(data.get("text", ""))
        except Exception:
            return "", ""

    def _select_state_matches(
        self,
        step: WorkflowStep,
        current_value: str,
        current_text: str,
        desired_value: str,
    ) -> bool:
        current_value = (current_value or "").strip().lower()
        current_text = (current_text or "").strip().lower()
        desired = {
            (desired_value or "").strip().lower(),
            (step.value or "").strip().lower(),
            (step.text or "").strip().lower(),
            (step.label or "").strip().lower(),
        }
        desired.discard("")
        if current_value in desired or current_text in desired:
            return True

        return self._is_address_autofill_select(step) and bool(current_value or current_text)

    def _should_keep_existing_select(
        self,
        step: WorkflowStep,
        current_value: str,
        current_text: str,
    ) -> bool:
        current_value = (current_value or "").strip()
        current_text = (current_text or "").strip()
        if not current_value and not current_text:
            return False

        desired_values = {
            (step.value or "").strip().lower(),
            (step.text or "").strip().lower(),
            (step.label or "").strip().lower(),
        }
        desired_values.discard("")
        if current_value.lower() in desired_values or current_text.lower() in desired_values:
            return True

        return self._is_address_autofill_select(step)

    def _is_chosen_step(self, step: WorkflowStep) -> bool:
        blob = " ".join([step.id, step.name, step.text, step.selector]).lower()
        return any(t in blob for t in ("_chzn", "_chosen", "chzn", "chosen", "subgroupid", "classid"))

    def _open_chosen_dropdown(self, page: Page, step: WorkflowStep) -> None:
        if not step.id:
            return
        for sel in (
            f"#{step.id}_chzn > a.chzn-single",
            f"#{step.id}_chosen > a.chosen-single",
            f"[id='{step.id}_chzn'] a",
        ):
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible(timeout=800):
                    btn.click(timeout=2_000)
                    time.sleep(0.2)
                    return
            except Exception:
                continue

    def _click_chosen_option(self, page: Page, target: str) -> bool:
        desired = (target or "").lower().strip()
        if not desired:
            return False
        try:
            for opt in page.locator(
                "ul.chzn-results li, ul.chosen-results li, li.active-result"
            ).all():
                if desired in (opt.inner_text() or "").lower():
                    opt.click(timeout=2_000)
                    return True
        except Exception:
            pass
        return False

    def _chosen_select(self, page: Page, step: WorkflowStep, value: str) -> bool:
        if not step.id:
            return False
        self._open_chosen_dropdown(page, step)
        time.sleep(0.15)
        return self._click_chosen_option(page, value)
