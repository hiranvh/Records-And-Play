"""
playback.session
----------------
Pure Playwright workflow execution — no LLM, no dynamic field scanning.

Architecture:
  FakerValueGenerator  — generates synthetic form values (Faker library)
  ElementLocator       — multi-strategy CSS/XPath element finding
  PlaybackSession      — full session lifecycle: browser, navigation, steps,
                         failure screenshots, Excel report

Form filling uses Faker-generated values for every data input field.
Credential fields (username/password) always use real credentials from
config.properties.  SELECT steps use the value recorded during capture since
valid options cannot be enumerated without inspecting the live DOM.

On any element-not-found failure:
  1. A JS overlay banner is injected into the page.
  2. A full-page screenshot is taken and saved under Screenshots/.
  3. The overlay is removed.
  4. The failure is written to the Excel report with reason and screenshot path.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from playwright.sync_api import Locator, Page, sync_playwright
from core.date_engine import build_realistic_timeline, infer_date_scenario, timeline_to_profile_fields

from .action_service import ActionService
from .auth_service import AuthService
from .discrepancy_service import DiscrepancyService
from .faker_values import FakerValueGenerator
from .input_service import InputService
from .locator_service import ElementLocator, LocatorService
from .models import (
    DiscrepancyRecord,
    PlaybackConfig,
    PlaybackResult,
    StepResult,
    StepType,
    WorkflowStep,
)
from .navigation_service import NavigationService
from .report_service import ReportService
from .screenshot_service import ScreenshotService
from .select_service import SelectService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PlaybackSession
# ---------------------------------------------------------------------------

class PlaybackSession:
    """
    Executes a recorded workflow using pure Playwright.

    Value-generation strategy
    -------------------------
    * INPUT / DATE  -> Faker-generated synthetic values
    * Credentials   -> real username/password from config.properties
    * SELECT        -> recorded value (valid options unknown at design time)
    * CLICK/TOGGLE  -> no fill value

    On failure (element not found)
    --------------------------------
    1. JS red-banner overlay injected into the live page
    2. Full-page screenshot saved to Screenshots/FAIL_<label>_<ts>.png
    3. Overlay removed
    4. Step recorded as FAIL with reason + screenshot path in Excel report

    Excel report
    ------------
    Written at session end to PlaybackConfig.excel_report_path or auto-generated
    under reports/<workflow>_<ts>.xlsx.

    Usage::

        config  = PlaybackConfig(...)
        session = PlaybackSession(config)
        result  = session.run()   # -> PlaybackResult
    """

    _RETRY_WAIT_S: float = 1.5
    _AJAX_TIMEOUT: int   = 3_000
    _NAV_TIMEOUT:  int   = 15_000
    _POST_NAV_TRANSITION_TIMEOUT_S: float = 6.0
    _POST_NAV_FIELD_READY_TIMEOUT_S: float = 8.0

    def __init__(self, config: PlaybackConfig) -> None:
        self._cfg = config
        self._loc = ElementLocator()
        self._locator_service = LocatorService(locator=self._loc, retry_wait_s=self._RETRY_WAIT_S)
        self._discrepancy_service = DiscrepancyService(self)
        self._input_service = InputService(self)
        self._select_service = SelectService(self)
        self._action_service = ActionService(self)
        self._auth_service = AuthService(self)
        self._date_memory: Dict[str, str] = {}

        seed, fill_order_mode, use_ollama, ollama_model, ollama_url, ollama_timeout = self._resolve_data_generation_config()
        self._faker = FakerValueGenerator(
            seed=seed,
            execution_profile=self._cfg.execution_profile,
            use_ollama=use_ollama,
            ollama_model=ollama_model,
            ollama_url=ollama_url,
            ollama_timeout_s=ollama_timeout,
        )
        self._fill_order_mode = fill_order_mode
        self._data_generation_meta = self._faker.metadata()
        self._data_generation_meta["fill_order_mode"] = self._fill_order_mode

        self._steps:  List[WorkflowStep]                    = []
        self._result: PlaybackResult                        = PlaybackResult()
        self._pairs:  List[Tuple[WorkflowStep, StepResult]] = []  # for Excel
        self._runtime_fields: List[Dict[str, Any]]          = []
        self._runtime_plan_context: str                     = ""
        self._pending_login_click: bool                     = False
        self._page_checkpoints: List[Dict[str, Any]]        = _parse_page_checkpoints(self._cfg.workflow_data)
        self._compared_page_signatures: Set[str]            = set()
        self._seen_discrepancy_keys: Set[str]               = set()
        self._pending_fill_verifications: Dict[str, Dict[str, Any]] = {}
        self._current_page_recorded_fields: Dict[str, Dict[str, Any]] = {}
        self._last_discrepancy_snapshot: Dict[str, Any] = {}

        mode, fail_missing_required, fail_new_required, fail_not_filled = self._resolve_replay_policy()
        self._result.replay_mode = mode
        self._result.fail_on_missing_required_fields = fail_missing_required
        self._result.fail_on_new_required_fields = fail_new_required
        self._result.fail_on_not_filled_fields = fail_not_filled

    # -- Public ----------------------------------------------------------------

    def run(self) -> PlaybackResult:
        """Execute the full workflow and return a PlaybackResult."""
        import time as _t
        start = _t.time()

        pw = browser = context = page = None
        try:
            try:
                pw      = sync_playwright().start()
                browser = pw.chromium.launch(headless=self._cfg.headless)
                context = browser.new_context()
                page    = context.new_page()
            except Exception as exc:
                self._result.status = "failed"
                self._result.error = f"Browser launch failed: {exc}"
                self._finalize_reports(start)
                return self._result

            try:
                self._steps = _parse_steps(self._cfg.workflow_data)
                self._apply_data_generation_metadata()
                self._run_workflow(page)
                self._result.status = "finished"
                final_shot = ScreenshotService.final_screenshot(page)
                if final_shot:
                    self._result.screenshot_path = final_shot
            except Exception as exc:
                self._result.status = "failed"
                self._result.error = str(exc)
                self._log(f"Session error: {exc}", "ERROR")

            self._finalize_reports(start)
            return self._result
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass

    # -- Workflow orchestration ------------------------------------------------

    def _run_workflow(self, page: Page) -> None:
        from core.constants import stop_execution_event

        self._log(f"Navigating to {self._cfg.start_url}")
        NavigationService.navigate(page, self._cfg.start_url, self._log)
        NavigationService.wait_ready(page, timeout_ms=self._NAV_TIMEOUT)
        self._maybe_auto_login(page)
        self._collect_page_discrepancies(page, trigger="initial")

        planned_steps = self._planned_steps_for_execution(self._steps)
        self._log(f"Executing {len(planned_steps)} recorded step(s)...")

        for ordinal, step in enumerate(planned_steps, start=1):
            if stop_execution_event.is_set():
                self._log("Stop signal received — halting.", "WARNING")
                break

            if not self._is_page_alive(page):
                self._log("Browser closed — aborting.", "ERROR")
                self._result.error = "Browser closed during playback"
                break

            if step.skip or step.executed:
                sr = StepResult(step.display_label, True, skipped=True, message="Pre-skipped")
                self._record(step, sr)
                self._result.steps_skipped += 1
                continue

            self._log(
                f"Step {ordinal}/{len(planned_steps)}: "
                f"{step.type} '{step.display_label}'"
            )

            obsolete_reason = self._obsolete_context_skip_reason(page, step)
            if obsolete_reason:
                sr = StepResult(step.display_label, True, skipped=True, message=obsolete_reason)
                step.executed = True
                self._record(step, sr)
                self._result.steps_skipped += 1
                self._log(f"  skipped: {obsolete_reason}", "WARNING")
                continue

            nav_step = self._is_nav_step(step)
            before_nav_snapshot: Optional[Dict[str, Any]] = None
            before_nav_url = ""
            post_nav_snapshot: Optional[Dict[str, Any]] = None

            if nav_step:
                self._verify_pending_fill_states(page, trigger=f"before_step_{step.index + 1}")
                self._collect_page_discrepancies(page, trigger=f"before_step_{step.index + 1}")
                before_nav_snapshot = self._scan_current_page_fields(page)
                try:
                    before_nav_url = page.url or ""
                except Exception:
                    before_nav_url = ""

            sr = self._execute_step(page, step)

            if sr.success and nav_step:
                reason, post_nav_snapshot = self._ensure_post_navigation_readiness(
                    page=page,
                    nav_step=step,
                    planned_steps=planned_steps,
                    nav_index=ordinal - 1,
                    before_snapshot=before_nav_snapshot,
                    before_url=before_nav_url,
                )
                if reason:
                    shot = ScreenshotService.failure_screenshot(page, step, reason, self._log)
                    sr = StepResult(step.display_label, False, message=reason, screenshot_path=shot)

            step.executed = True
            self._record(step, sr)

            if sr.skipped:
                self._result.steps_skipped += 1
            elif sr.success:
                self._result.steps_executed += 1
                self._log(f"  ok", "SUCCESS")
                if nav_step:
                    self._clear_pending_fill_states_if_page_changed(
                        before_nav_snapshot,
                        post_nav_snapshot or self._scan_current_page_fields(page),
                    )
                    self._collect_page_discrepancies(page, trigger=f"step_{step.index + 1}")
            else:
                self._result.steps_failed += 1
                self._log(f"  FAIL: {sr.message}", "ERROR")

            time.sleep(0.2 / max(self._cfg.speed_factor, 0.1))

        self._verify_pending_fill_states(page, trigger="final")
        self._collect_page_discrepancies(page, trigger="final")

    def _apply_data_generation_metadata(self) -> None:
        data = dict(self._data_generation_meta or {})
        self._result.data_generation = data

        src = str(data.get("source") or "faker")
        seed = data.get("seed")
        mode = str(data.get("fill_order_mode") or "recorded")
        self._log(f"Data generation: source={src}, seed={seed}, fill_order={mode}")

        for warning in data.get("warnings") or []:
            self._log(f"Data generation warning: {warning}", "WARNING")
        for correction in data.get("corrections") or []:
            self._log(f"Auto-correction applied: {correction}", "WARNING")

    def _planned_steps_for_execution(self, steps: List[WorkflowStep]) -> List[WorkflowStep]:
        if self._fill_order_mode != "dependency":
            return list(steps)

        ordered: List[WorkflowStep] = []
        fill_chunk: List[WorkflowStep] = []

        for step in steps:
            if self._is_reorderable_fill_step(step):
                fill_chunk.append(step)
                continue

            if fill_chunk:
                ordered.extend(
                    sorted(
                        fill_chunk,
                        key=lambda s: (self._fill_dependency_priority(s), s.index),
                    )
                )
                fill_chunk = []

            ordered.append(step)

        if fill_chunk:
            ordered.extend(
                sorted(
                    fill_chunk,
                    key=lambda s: (self._fill_dependency_priority(s), s.index),
                )
            )

        return ordered

    @staticmethod
    def _is_reorderable_fill_step(step: WorkflowStep) -> bool:
        if step.is_credential_field:
            return False
        return step.step_type in (StepType.INPUT, StepType.DATE, StepType.SELECT, StepType.TOGGLE)

    def _fill_dependency_priority(self, step: WorkflowStep) -> int:
        blob = self._norm(" ".join([step.id, step.name, step.label, step.text, step.placeholder, step.selector]))
        if any(token in blob for token in ("dob", "birthdate", "dateofbirth", "birth_date")):
            return 10
        if any(token in blob for token in ("hiredate", "dateofhire", "hire_date", "employmentdate", "startdate")):
            return 20
        if any(token in blob for token in ("billinglocation", "subgroup", "subgroupid")):
            return 30
        if any(token in blob for token in ("employeeclass", "classid", "employeetype")):
            return 40
        if any(token in blob for token in ("effectivedate", "effective_date", "coverageeffective")):
            return 50
        if any(token in blob for token in ("payment", "bank", "card", "concurrentcoverage")):
            return 60
        return 100

    # -- Step dispatcher -------------------------------------------------------

    def _execute_step(self, page: Page, step: WorkflowStep) -> StepResult:
        if step.is_login_submit and self._password_coming(step):
            self._pending_login_click = True
            return StepResult(
                step.display_label, True, skipped=True,
                message="Deferred until password filled",
            )

        step = self._maybe_override_group(step)
        t = step.step_type

        if t in (StepType.INPUT, StepType.DATE):
            return self._fill_input(page, step)
        if t == StepType.SELECT:
            return self._fill_select(page, step)
        if t in (StepType.CLICK, StepType.CLICK_LINK):
            return self._do_click(page, step)
        if t == StepType.TOGGLE:
            return self._do_toggle(page, step)

        return StepResult(step.display_label, False, message=f"Unknown type: {step.type}")

    # -- Input / fill handlers -------------------------------------------------

    def _fill_input(self, page: Page, step: WorkflowStep) -> StepResult:
        return self._input_service._fill_input(page, step)

    def _fill_select(self, page: Page, step: WorkflowStep) -> StepResult:
        return self._select_service._fill_select(page, step)

    def _do_click(self, page: Page, step: WorkflowStep) -> StepResult:
        return self._action_service._do_click(page, step)

    def _try_dashboard_shortcut(self, page: Page, step: WorkflowStep) -> Optional[StepResult]:
        return self._action_service._try_dashboard_shortcut(page, step)

    def _do_toggle(self, page: Page, step: WorkflowStep) -> StepResult:
        return self._action_service._do_toggle(page, step)

    # -- Login -----------------------------------------------------------------

    def _maybe_auto_login(self, page: Page) -> None:
        self._auth_service._maybe_auto_login(page)

    def _wait_for_post_login_targets(self, page: Page, timeout_s: float = 12.0) -> None:
        self._auth_service._wait_for_post_login_targets(page, timeout_s)

    def _click_login_button(self, page: Page) -> bool:
        return self._auth_service._click_login_button(page)

    def _ensure_login_success_or_raise(
        self,
        page: Page,
        before_url: str = "",
        context: str = "login",
        timeout_s: float = 12.0,
        capture_on_failure: bool = False,
    ) -> None:
        self._auth_service._ensure_login_success_or_raise(
            page=page,
            before_url=before_url,
            context=context,
            timeout_s=timeout_s,
            capture_on_failure=capture_on_failure,
        )

    # -- Group / Chosen helpers ------------------------------------------------

    def _click_group_row(self, page: Page) -> bool:
        desired = (self._cfg.group_name or "").strip().lower()
        if not desired:
            return False
        try:
            for row in page.locator("table tbody tr").all():
                if desired in (row.inner_text() or "").lower():
                    link = row.locator("a").first
                    if link.count() > 0:
                        link.click(timeout=5_000)
                        return True
        except Exception:
            pass
        return False

    def _resolve_fill_locator(self, page: Page, step: WorkflowStep, el: Locator) -> Locator:
        return self._input_service._resolve_fill_locator(page, step, el)

    def _set_text_input_value(self, el: Locator, value: str) -> None:
        self._input_service._set_text_input_value(el, value)

    @staticmethod
    def _read_input_value(el: Locator) -> str:
        return InputService._read_input_value(el)

    @staticmethod
    def _input_value_matches(step: WorkflowStep, actual: str, desired: str) -> bool:
        return InputService._input_value_matches(step, actual, desired)

    def _find_radio_option(self, page: Page, step: WorkflowStep) -> Optional[Locator]:
        raw_value = self._norm(step.value)
        desired_tokens = self._radio_choice_tokens(step)
        desired_bool = self._radio_bool_from_tokens(desired_tokens)

        groups: List[Locator] = []
        if step.name:
            groups.append(page.locator(f"input[type='radio'][name='{step.name}']"))
        if step.id:
            groups.append(page.locator(f"input[type='radio'][id='{step.id}']"))
        if self._is_product_plan_step(step):
            groups.append(page.locator("input[type='radio'][name*='ProductLst'][name*='SelectedLineId']"))
            groups.append(page.locator("input[type='radio'][id*='ProductLst'][id*='SelectedLineId']"))
        if self._is_payment_step(step):
            groups.append(page.locator("input[type='radio'][name*='payment'], input[type='radio'][id^='rd_']"))

        for group in groups:
            matched = self._match_radio_by_label_tokens(group, desired_tokens)
            if matched is not None:
                return matched

        if desired_bool is not None:
            bool_value = "true" if desired_bool else "false"
            for group in groups:
                matched = self._match_radio_by_value(group, bool_value)
                if matched is not None:
                    return matched

        if raw_value:
            for group in groups:
                matched = self._match_radio_by_value(group, raw_value)
                if matched is not None:
                    return matched

        for group in groups:
            try:
                loc = group.first
                if loc.count() > 0:
                    return loc
            except Exception:
                continue
        return None

    def _radio_choice_tokens(self, step: WorkflowStep) -> List[str]:
        tokens: List[str] = []
        for raw in (step.text, step.label):
            normalized = self._norm(raw)
            if not normalized:
                continue
            if normalized not in tokens:
                tokens.append(normalized)
            for word in re.findall(r"[a-z0-9]+", normalized):
                if word in {"yes", "no", "true", "false", "1", "0", "on", "off"} and word not in tokens:
                    tokens.append(word)
        return tokens

    @staticmethod
    def _radio_bool_from_tokens(tokens: List[str]) -> Optional[bool]:
        for token in tokens:
            if token in {"yes", "true", "1", "on"}:
                return True
            if token in {"no", "false", "0", "off"}:
                return False
        return None

    def _match_radio_by_label_tokens(self, group: Locator, tokens: List[str]) -> Optional[Locator]:
        if not tokens:
            return None
        try:
            count = min(group.count(), 25)
        except Exception:
            return None

        for idx in range(count):
            candidate = group.nth(idx)
            label = self._radio_option_label(candidate)
            try:
                value = self._norm(candidate.get_attribute("value") or "")
            except Exception:
                value = ""

            if label in tokens or value in tokens:
                return candidate
            if label and any(tok for tok in tokens if len(tok) > 1 and tok in label):
                return candidate
        return None

    def _match_radio_by_value(self, group: Locator, desired_value: str) -> Optional[Locator]:
        target = self._norm(desired_value)
        if not target:
            return None

        try:
            count = min(group.count(), 25)
        except Exception:
            return None

        for idx in range(count):
            candidate = group.nth(idx)
            try:
                value = self._norm(candidate.get_attribute("value") or "")
            except Exception:
                value = ""
            if value == target:
                return candidate
        return None

    def _radio_option_label(self, candidate: Locator) -> str:
        try:
            raw = candidate.evaluate(
                r"""(node) => {
                    const text = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                    let label = '';
                    if (node.labels && node.labels.length) {
                        label = (node.labels[0].innerText || node.labels[0].textContent || '');
                    }
                    if (!label) {
                        const aria = node.getAttribute('aria-label');
                        if (aria) label = aria;
                    }
                    if (!label) {
                        const parent = node.closest('label');
                        if (parent) label = (parent.innerText || parent.textContent || '');
                    }
                    if (!label) {
                        const next = node.nextElementSibling;
                        if (next && (next.tagName === 'LABEL' || next.tagName === 'SPAN')) {
                            label = (next.innerText || next.textContent || '');
                        }
                    }
                    if (!label) {
                        const prev = node.previousElementSibling;
                        if (prev && (prev.tagName === 'LABEL' || prev.tagName === 'SPAN')) {
                            label = (prev.innerText || prev.textContent || '');
                        }
                    }
                    return text(label);
                }"""
            )
            return self._norm(raw)
        except Exception:
            return ""

    @staticmethod
    def _is_address_autofill_select(step: WorkflowStep) -> bool:
        return SelectService._is_address_autofill_select(step)

    def _mapped_address_select_value(self, page: Page, step: WorkflowStep) -> str:
        return self._select_service._mapped_address_select_value(page, step)

    def _post_toggle_stabilize(self, page: Page, step: WorkflowStep, want: bool) -> None:
        if self._is_product_plan_step(step):
            NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
            self._wait_for_plan_section_ready(page, timeout_s=6.0)
            return
        if self._is_payment_step(step):
            NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
            self._wait_for_payment_section_ready(page, timeout_s=6.0)
            return
        if not want:
            return
        if step.id == "contactSameAsHomeAddress":
            self._restore_home_address_selects(page)

    def _restore_home_address_selects(self, page: Page) -> None:
        from core.constants import ZIP_LOCATION_DATA

        try:
            zip_loc = page.locator("#EmployeeVM_Person_HomeAddress_ZipCode").first
            if zip_loc.count() == 0:
                return
            zip_code = (zip_loc.input_value() or "").strip()
        except Exception:
            return

        data = ZIP_LOCATION_DATA.get(zip_code) or {}
        if not data:
            return

        time.sleep(0.35)
        for field_key, select_id in (
            ("county", "EmployeeVM_Person_HomeAddress_County"),
            ("city", "EmployeeVM_Person_HomeAddress_City"),
            ("state", "EmployeeVM_Person_HomeAddress_StateCode"),
        ):
            desired = str(data.get(field_key, "") or "").strip()
            if not desired:
                continue
            try:
                loc = page.locator(f"#{select_id}").first
                if loc.count() == 0:
                    continue
                current_value, current_text = self._read_select_state(loc)
                if current_value or current_text:
                    continue
                self._set_select_via_js(loc, desired, desired)
            except Exception:
                continue

    def _wait_for_select_options(
        self,
        el: Locator,
        step: WorkflowStep,
        desired_value: str,
        timeout_s: float = 5.0,
    ) -> None:
        self._select_service._wait_for_select_options(el, step, desired_value, timeout_s)

    def _has_chosen_widget(self, page: Page, step: WorkflowStep) -> bool:
        return self._select_service._has_chosen_widget(page, step)

    @staticmethod
    def _read_select_options(el: Locator) -> List[Tuple[str, str]]:
        return SelectService._read_select_options(el)

    @staticmethod
    def _set_select_via_js(el: Locator, desired_value: str, desired_text: str = "") -> None:
        SelectService._set_select_via_js(el, desired_value, desired_text)

    @staticmethod
    def _read_select_state(el: Locator) -> Tuple[str, str]:
        return SelectService._read_select_state(el)

    def _select_state_matches(
        self,
        step: WorkflowStep,
        current_value: str,
        current_text: str,
        desired_value: str,
    ) -> bool:
        return self._select_service._select_state_matches(step, current_value, current_text, desired_value)

    def _should_keep_existing_select(
        self,
        step: WorkflowStep,
        current_value: str,
        current_text: str,
    ) -> bool:
        return self._select_service._should_keep_existing_select(step, current_value, current_text)

    @staticmethod
    def _set_checked_via_js(el: Locator, want: bool) -> None:
        el.evaluate(
            """(node, checked) => {
                node.checked = checked;
                node.dispatchEvent(new Event('input', { bubbles: true }));
                node.dispatchEvent(new Event('change', { bubbles: true }));
                node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
            }""",
            want,
        )

    def _is_chosen_step(self, step: WorkflowStep) -> bool:
        return self._select_service._is_chosen_step(step)

    def _open_chosen_dropdown(self, page: Page, step: WorkflowStep) -> None:
        self._select_service._open_chosen_dropdown(page, step)

    def _click_chosen_option(self, page: Page, target: str) -> bool:
        return self._select_service._click_chosen_option(page, target)

    def _chosen_select(self, page: Page, step: WorkflowStep, value: str) -> bool:
        return self._select_service._chosen_select(page, step, value)

    # -- jQuery UI datepicker helpers ------------------------------------------

    @staticmethod
    def _is_datepicker_select(step: WorkflowStep) -> bool:
        """True when the step targets a jQuery UI datepicker year/month select."""
        sel = step.selector or ""
        return "ui-datepicker-year" in sel or "ui-datepicker-month" in sel

    @staticmethod
    def _is_datepicker_day_click(step: WorkflowStep) -> bool:
        """True when the step targets a jQuery UI datepicker day link."""
        if step.step_type not in (StepType.CLICK, StepType.CLICK_LINK):
            return False
        sel = step.selector or ""
        return "ui-state-default" in sel and not step.id and not step.name

    def _handle_datepicker_step(self, page: Page, step: WorkflowStep) -> StepResult:
        """Skip datepicker widget steps and fill all empty date inputs directly.

        The recorder captures jQuery UI datepicker widget interactions (year/month
        selects, day clicks) but NOT the initial field focus that opens the picker.
        Rather than trying to replay the widget, we locate every empty date input
        visible on the current page and fill it with a Faker-generated date, then
        mark all consecutive datepicker steps as pre-skipped so they are not
        attempted again.
        """
        # Mark all consecutive following datepicker steps as skip so they are
        # not attempted (this step is the first of a cluster).
        idx = step.index + 1
        while idx < len(self._steps):
            s = self._steps[idx]
            if self._is_datepicker_select(s) or self._is_datepicker_day_click(s):
                s.skip = True
                idx += 1
            else:
                break

        filled = self._fill_empty_date_inputs(page)
        msg = (
            f"Datepicker skipped — {filled} date field(s) pre-filled"
            if filled
            else "Datepicker skipped — no empty date fields found"
        )
        return StepResult(step.display_label, True, skipped=True, message=msg)

    def _fill_empty_date_inputs(self, page: Page) -> int:
        """Find all empty visible date inputs and fill them with Faker dates.

        Tries multiple selectors; skips already-filled and invisible fields;
        de-dupes by element ID to avoid filling the same field twice.
        Returns the number of fields successfully filled.
        """
        selectors = [
            "input[placeholder='MM/DD/YYYY']",
            "input[placeholder='MM/DD/YY']",
            "input.hasDatepicker",
            "input[data-val-date]",
            "input[id*='DateOfBirth']",
            "input[id*='HireDate']",
            "input[id*='RetireDate']",
            "input[id*='EffectiveDate']",
            "input[id*='EnrollmentWindow']",
        ]

        planned: List[Tuple[int, str, int, Locator, str]] = []
        seen_keys: Set[str] = set()
        ordinal = 0

        for sel in selectors:
            try:
                locs = page.locator(sel)
                count = locs.count()
                for i in range(min(count, 20)):
                    candidate = locs.nth(i)
                    try:
                        if not candidate.is_visible(timeout=200):
                            continue
                        field_id = candidate.get_attribute("id", timeout=200) or ""
                        field_name = candidate.get_attribute("name", timeout=200) or ""
                        placeholder = candidate.get_attribute("placeholder", timeout=200) or ""
                        stable_key = field_id or field_name or f"{sel}:{i}"
                        if stable_key in seen_keys:
                            continue

                        val = (candidate.input_value(timeout=300) or "").strip()
                        if val:
                            seen_keys.add(stable_key)
                            continue

                        try:
                            nearby_text = candidate.evaluate(
                                """(node) => {
                                    const container = node.closest('div, td, li, section, article');
                                    return String((container && container.innerText) || '').slice(0, 200);
                                }"""
                            ) or ""
                        except Exception:
                            nearby_text = ""

                        blob = " ".join(
                            [
                                field_id,
                                field_name,
                                placeholder,
                                nearby_text,
                            ]
                        ).lower()
                        planned.append((self._date_blob_priority(blob), stable_key, ordinal, candidate, blob))
                        seen_keys.add(stable_key)
                        ordinal += 1
                    except Exception:
                        continue
            except Exception:
                continue

        planned.sort(key=lambda row: (row[0], row[1], row[2]))

        filled = 0
        for _, _, _, candidate, blob in planned:
            try:
                if not candidate.is_visible(timeout=200):
                    continue
                date_val = self._coordinated_date_value(blob)
                candidate.click(timeout=2_000, force=True)
                time.sleep(0.1)
                candidate.fill(date_val, timeout=2_000)
                candidate.press("Tab")
                time.sleep(0.15)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                filled += 1
            except Exception:
                continue

        if filled > 0:
            NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
        return filled

    def _date_blob_priority(self, blob: str) -> int:
        norm_blob = self._norm(blob)
        if any(token in norm_blob for token in ("dob", "birthdate", "dateofbirth", "birth_date")):
            return 10
        if any(token in norm_blob for token in ("hiredate", "dateofhire", "hire_date", "employmentdate", "startdate")):
            return 20
        if any(token in norm_blob for token in ("effectivedate", "effective_date", "coverageeffective")):
            return 50
        if any(token in norm_blob for token in ("enrollmentdate", "enrollment_date", "coveragestartdate", "electiondate")):
            return 55
        if any(token in norm_blob for token in ("payment", "bank", "card")):
            return 60
        return 100

    def _coordinated_date_value(self, blob: str) -> str:
        norm_blob = self._norm(blob)
        if not self._date_memory.get("effective_date"):
            profile_dates = {
                "dob_date": self._date_memory.get("dob_date") or self._faker.profile_date("dob"),
                "hire_date": self._date_memory.get("hire_date") or self._faker.profile_date("hire"),
                "effective_date": self._date_memory.get("effective_date") or self._faker.profile_date("effective"),
                "enrollment_date": self._date_memory.get("enrollment_date") or self._faker.profile_date("enrollment"),
                "retirement_date": self._date_memory.get("retirement_date") or self._faker.profile_date("retirement"),
            }

            hint_parts = [
                str((self._cfg.workflow_data or {}).get("name", "") or ""),
                str((self._cfg.execution_profile or {}).get("_workflow_name", "") or ""),
                str((self._cfg.execution_profile or {}).get("workflow_name", "") or ""),
                str((self._cfg.execution_profile or {}).get("start_url", "") or ""),
            ]
            scenario = infer_date_scenario(
                hint_text=" ".join(part for part in hint_parts if part),
                hint_fields=list((self._cfg.execution_profile or {}).keys()),
            )

            seed = int(getattr(self._faker, "seed", 0) or 1)
            timeline, _ = build_realistic_timeline(
                rand=random.Random(seed),
                overrides=profile_dates,
                scenario=scenario,
            )
            self._date_memory.update(timeline_to_profile_fields(timeline))

        if any(token in norm_blob for token in ("birth", "dob")):
            return self._date_memory["dob_date"]
        if any(token in norm_blob for token in ("hiredate", "dateofhire", "hire_date", "employmentdate", "startdate")):
            return self._date_memory["hire_date"]
        if any(token in norm_blob for token in ("retiredate", "retirement", "terminationdate", "termdate")):
            return self._date_memory["retirement_date"]
        if "enrollmentwindowstart" in norm_blob or "enrollment_window_start" in norm_blob:
            return self._date_memory["enrollment_window_start"]
        if "enrollmentwindowend" in norm_blob or "enrollment_window_end" in norm_blob:
            return self._date_memory["enrollment_window_end"]
        if any(token in norm_blob for token in ("enrollmentdate", "enrollment_date", "coveragestartdate", "electiondate")):
            return self._date_memory["enrollment_date"]
        if any(token in norm_blob for token in ("effectivedate", "effective_date", "coverageeffective")):
            return self._date_memory["effective_date"]

        return self._date_memory["effective_date"]

    # -- Address ZIP auto-fill -------------------------------------------------

    def _is_employee_submission_step(self, page: Page, step: WorkflowStep) -> bool:
        current_url = (page.url or "").lower()
        if "/new/employees" not in current_url:
            return False

        blob = " ".join([step.id, step.name, step.text, step.selector]).lower()
        return any(token in blob for token in (
            "btnactivate",
            "activate & enroll",
            "activateandenroll",
            "btnsubmit",
        ))

    def _prepare_employee_form_for_submission(self, page: Page) -> None:
        phone_digits = re.sub(r"\D+", "", self._faker.identity.get("phone", ""))
        phone_value = self._faker.identity.get("phone", "")
        ssn_value = self._faker.identity.get("ssn", "")
        work_ext_value = (phone_digits[-4:] or "1234")[:6]
        bargaining_value = self._faker.profile.get("bargaining_unit", "") or f"Unit{(phone_digits[-3:] or '001')}"

        self._ensure_field_value(
            page,
            ["#EmployeeVM_Person_SSN_mask", "#EmployeeVM_Person_SSN"],
            ssn_value,
            lambda current: not re.search(r"\d", current or ""),
        )
        self._ensure_field_value(
            page,
            ["#EmployeeVM_Person_Phone"],
            phone_value,
            lambda current: not re.search(r"\d", current or ""),
        )
        self._ensure_field_value(
            page,
            ["#EmployeeVM_Person_AltPhone2"],
            phone_value,
            lambda current: not re.search(r"\d", current or ""),
        )
        self._ensure_field_value(
            page,
            ["#EmployeeVM_BargainingUnit"],
            bargaining_value,
            lambda current: not current or bool(re.search(r"[^A-Za-z0-9]", current)),
        )
        self._ensure_field_value(
            page,
            ["#EmployeeVM_Person_Ext1"],
            work_ext_value,
            lambda current: not re.fullmatch(r"\d{1,6}", (current or "").strip()),
        )
        NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)

    def _ensure_field_value(
        self,
        page: Page,
        selectors: List[str],
        desired_value: str,
        needs_update,
    ) -> bool:
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0 or not loc.is_visible(timeout=500):
                    continue
                current_value = self._read_input_value(loc)
                if not needs_update(current_value):
                    return True
                self._set_text_input_value(loc, desired_value)
                try:
                    loc.press("Tab", timeout=700)
                except Exception:
                    pass
                return True
            except Exception:
                continue
        return False

    def _employee_form_validation_errors(self, page: Page) -> List[str]:
        selectors = (
            ".field-validation-error",
            ".text-danger",
            ".validation-summary-errors li",
            ".help-block",
        )
        seen: List[str] = []
        for selector in selectors:
            try:
                texts = page.locator(selector).all_inner_texts()
            except Exception:
                continue
            for text in texts:
                normalized = re.sub(r"\s+", " ", str(text or "")).strip()
                if not normalized or normalized in seen:
                    continue
                if any(token in normalized.lower() for token in ("required", "invalid", "must contain")):
                    seen.append(normalized)
        return seen

    def _employee_submission_advanced(self, page: Page, before_url: str) -> bool:
        current_url = page.url or ""
        if current_url != before_url:
            return True

        for selector in (
            "a.planAccordian",
            "#BtnGotoProfile",
            "#BtnSubmitEnrollment",
            "#DropDownPlanYear",
        ):
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    def _mark_stale_employee_followup_steps(self, submitted_step: WorkflowStep) -> int:
        skipped = 0
        submitted_page = self._workflow_step_page_identity(submitted_step)
        for candidate in self._steps[submitted_step.index + 1:]:
            if candidate.executed or candidate.skip:
                continue
            candidate_page = self._workflow_step_page_identity(candidate)
            if self._page_identity_changed(submitted_page, candidate_page):
                break
            if not self._is_stale_employee_followup_step(candidate):
                break
            candidate.skip = True
            skipped += 1
        return skipped

    @staticmethod
    def _workflow_step_page_identity(step: Optional[WorkflowStep]) -> Tuple[str, str, str]:
        if not step:
            return ("", "", "")
        return (step.page_id or "", step.page_url or "", step.page_title or "")

    @staticmethod
    def _page_identity_changed(before: Tuple[str, str, str], after: Tuple[str, str, str]) -> bool:
        if not any(before) or not any(after):
            return False
        if before[1] and after[1] and before[1] != after[1]:
            return True
        if before[0] and after[0] and before[0] != after[0]:
            return True
        return bool(before[2] and after[2] and before[2] != after[2])

    def _is_stale_employee_followup_step(self, step: WorkflowStep) -> bool:
        if self._is_datepicker_select(step) or self._is_datepicker_day_click(step):
            return True

        if step.step_type not in (StepType.INPUT, StepType.SELECT, StepType.TOGGLE, StepType.DATE):
            return False

        blob = " ".join([step.id, step.name, step.selector]).lower()
        return "employeevm_" in blob or "employeevm." in blob

    def _ensure_zip_filled(self, page: Page, step: WorkflowStep) -> None:
        """Fill the 5-digit ZIP field if it is empty so AJAX populates address dropdowns.

        For MailingAddress, falls back to reading the HomeAddress ZIP value so the
        same AJAX-driven county/city/state options are populated.
        """
        from core.constants import DEFAULT_VALID_ZIP

        blob = " ".join([step.id, step.name]).lower()
        if "homeaddress" in blob:
            zip_selectors = [
                "#EmployeeVM_Person_HomeAddress_ZipCode",
                "[name='EmployeeVM.Person.HomeAddress.ZipCode']",
            ]
            source_zip = DEFAULT_VALID_ZIP
        elif "mailingaddress" in blob:
            zip_selectors = [
                "#EmployeeVM_Person_MailingAddress_ZipCode",
                "#EmployeeVM_Person_MailingAddress_NonUSZipCode",
                "[name='EmployeeVM.Person.MailingAddress.ZipCode']",
                "[name='EmployeeVM.Person.MailingAddress.NonUSZipCode']",
            ]
            # Read the already-filled HomeAddress ZIP so mailing address gets the
            # same ZIP-driven county/city/state options from AJAX.
            try:
                home_loc = page.locator(
                    "#EmployeeVM_Person_HomeAddress_ZipCode, "
                    "[name='EmployeeVM.Person.HomeAddress.ZipCode']"
                ).first
                source_zip = (home_loc.input_value(timeout=800) or "").strip() or DEFAULT_VALID_ZIP
            except Exception:
                source_zip = DEFAULT_VALID_ZIP
        else:
            return

        for sel in zip_selectors:
            try:
                zip_loc = page.locator(sel).first
                if zip_loc.count() == 0:
                    continue
                zip_val = (zip_loc.input_value(timeout=500) or "").strip()
                if zip_val:
                    return  # already filled — nothing to do
                if not zip_loc.is_visible(timeout=800):
                    continue
                zip_loc.click(timeout=2_000, force=True)
                zip_loc.fill(source_zip, timeout=3_000)
                zip_loc.press("Tab", timeout=1_000)
                NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                time.sleep(1.2)   # allow AJAX-driven dropdowns to populate
                return
            except Exception:
                continue

    # -- Excel report ----------------------------------------------------------

    def _write_excel(self) -> str:
        """Build the Excel report from accumulated step/result pairs."""
        saved = ReportService.write_excel(
            cfg=self._cfg,
            pairs=self._pairs,
            duration_seconds=self._result.duration_seconds,
            log=self._log,
        )
        self._result.excel_report_path = saved or ""
        return saved or ""

    def _write_master_data(self) -> str:
        """Append the run to the persistent replay master workbook."""
        try:
            from .master_data_reporter import append_master_data_run

            saved = append_master_data_run(
                cfg=self._cfg,
                result=self._result,
                pairs=self._pairs,
                runtime_fields=self._runtime_fields,
            )
            self._result.master_data_path = saved or ""
            self._log(f"Master data workbook: {saved}")
            return saved or ""
        except Exception as exc:
            self._log(f"Master data workbook error: {exc}", "WARNING")
            return ""

    def _finalize_reports(self, start_time: float) -> None:
        self._result.duration_seconds = time.time() - start_time
        self._result.runtime_data = {"fields": list(self._runtime_fields)}
        self._write_excel()
        self._write_master_data()

    # -- Utilities -------------------------------------------------------------

    def _maybe_override_group(self, step: WorkflowStep) -> WorkflowStep:
        if not self._cfg.group_name:
            return step
        if step.step_type != StepType.CLICK_LINK or step.tag != "a":
            return step
        txt = (step.text or "").lower()
        skip_words = ("employee administration", "person_add", "add employee", "enroll")
        if any(w in txt for w in skip_words):
            return step
        group_words = ("industries", "school", "services", "group", "inc", "corp", "ltd")
        if any(w in txt for w in group_words):
            from dataclasses import replace
            return replace(step, text=self._cfg.group_name, label=self._cfg.group_name,
                           selector="", id="", name="")
        return step

    def _find_with_retry(
        self,
        page: Page,
        step: WorkflowStep,
        retries: int = 2,
        allow_hidden_custom_widget: bool = False,
    ) -> Optional[Locator]:
        return self._locator_service.find_with_retry(
            page=page,
            step=step,
            retries=retries,
            log=self._log,
            on_healed=self._record_healed_selector,
            allow_hidden_custom_widget=allow_hidden_custom_widget,
        )

    def _is_nav_step(self, step: WorkflowStep) -> bool:
        if step.step_type in (StepType.INPUT, StepType.SELECT, StepType.TOGGLE):
            return False
        blob = " ".join([step.id, step.name, step.text, step.selector]).lower()
        nav_tokens = ("submit", "next", "continue", "login", "sign in", "btnadd", "personadd", "activate")
        return (
            (step.input_type or "").lower() == "submit"
            or any(t in blob for t in nav_tokens)
        )

    def _ensure_post_navigation_readiness(
        self,
        page: Page,
        nav_step: WorkflowStep,
        planned_steps: List[WorkflowStep],
        nav_index: int,
        before_snapshot: Optional[Dict[str, Any]],
        before_url: str,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        NavigationService.wait_ready(page, timeout_ms=self._NAV_TIMEOUT)

        transitioned, snapshot = self._wait_for_post_navigation_transition(
            page,
            before_snapshot=before_snapshot,
            before_url=before_url,
            timeout_s=self._POST_NAV_TRANSITION_TIMEOUT_S,
        )

        next_idx, next_step = self._next_executable_field_step(planned_steps, nav_index)
        if next_step is None:
            return None, snapshot

        gate_step = None
        gate_idx = self._prioritize_required_gate_step(page, planned_steps, nav_index)
        if gate_idx is not None and gate_idx == nav_index + 1:
            gate_step = planned_steps[gate_idx]
            next_idx, next_step = self._next_executable_field_step(planned_steps, nav_index)
            if gate_step is not None:
                self._log(
                    f"  prioritized required gate field '{gate_step.display_label}' after navigation",
                    "WARNING",
                )

        if next_step is None:
            return None, snapshot

        readiness_ok, signals, latest_snapshot = self._wait_for_post_navigation_outcome(
            page,
            before_snapshot=before_snapshot,
            before_url=before_url,
            next_step=next_step,
            timeout_s=self._POST_NAV_FIELD_READY_TIMEOUT_S,
            transitioned=transitioned,
            baseline_snapshot=snapshot,
        )

        if readiness_ok:
            self._suppress_stale_post_navigation_preface(
                page=page,
                planned_steps=planned_steps,
                nav_index=nav_index,
                baseline_snapshot=latest_snapshot,
            )
            return None, latest_snapshot

        if not self._navigation_outcome_is_blocked(signals, transitioned):
            self._log(
                f"Post-navigation readiness timeout after '{nav_step.display_label}', continuing (signals: {self._format_navigation_signals(signals)}).",
                "WARNING",
            )
            fallback_snapshot = self._scan_current_page_fields(page)
            resolved_snapshot = fallback_snapshot if isinstance(fallback_snapshot, dict) and fallback_snapshot else latest_snapshot
            self._suppress_stale_post_navigation_preface(
                page=page,
                planned_steps=planned_steps,
                nav_index=nav_index,
                baseline_snapshot=resolved_snapshot,
            )
            return None, resolved_snapshot

        reason_parts = [f"Post-navigation readiness timeout after '{nav_step.display_label}'"]
        if not transitioned:
            reason_parts.append("route/page transition not observed")
        reason_parts.append(f"next recorded field '{next_step.display_label}' is not visible/actionable")
        if gate_step is not None:
            reason_parts.append(f"required gate field '{gate_step.display_label}' was prioritized")
        reason_parts.append(f"signals: {self._format_navigation_signals(signals)}")

        return "; ".join(reason_parts), latest_snapshot

    def _wait_for_post_navigation_outcome(
        self,
        page: Page,
        before_snapshot: Optional[Dict[str, Any]],
        before_url: str,
        next_step: Optional[WorkflowStep],
        timeout_s: float,
        transitioned: bool,
        baseline_snapshot: Optional[Dict[str, Any]],
    ) -> Tuple[bool, Dict[str, bool], Dict[str, Any]]:
        deadline = time.time() + max(timeout_s, 0.5)
        last_snapshot = baseline_snapshot if isinstance(baseline_snapshot, dict) else {}
        if not last_snapshot:
            scan = self._scan_current_page_fields(page)
            if isinstance(scan, dict):
                last_snapshot = scan

        initial_dom_signature = InputService._dom_signature(page)
        last_dom_signature = initial_dom_signature
        stable_hits = 0
        saw_busy_overlay = False
        saw_dom_change = False
        last_signals: Dict[str, bool] = {
            "route_changed": bool(transitioned),
            "modal_open": False,
            "confirmation_visible": False,
            "content_changed": False,
            "spinner_settled": False,
            "new_controls_visible": False,
            "success_toast_visible": False,
            "known_next_container": False,
            "next_step_actionable": False,
        }

        while time.time() < deadline:
            snapshot = self._scan_current_page_fields(page)
            if isinstance(snapshot, dict) and snapshot:
                last_snapshot = snapshot

            dom_signature = InputService._dom_signature(page)
            if dom_signature and dom_signature != initial_dom_signature:
                saw_dom_change = True
            if dom_signature and dom_signature == last_dom_signature:
                stable_hits += 1
            else:
                stable_hits = 0
                last_dom_signature = dom_signature
            dom_stable = stable_hits >= 2

            overlays_cleared = self._loading_overlays_cleared(page)
            if not overlays_cleared:
                saw_busy_overlay = True

            signals = self._navigation_success_signals(
                page=page,
                before_snapshot=before_snapshot,
                before_url=before_url,
                snapshot=last_snapshot,
                next_step=next_step,
                transitioned=transitioned,
                dom_stable=dom_stable,
                saw_busy_overlay=saw_busy_overlay,
                saw_dom_change=saw_dom_change,
                overlays_cleared=overlays_cleared,
            )
            last_signals = signals

            if any(signals.values()):
                return True, signals, last_snapshot

            time.sleep(0.2)

        return False, last_signals, last_snapshot

    def _navigation_success_signals(
        self,
        page: Page,
        before_snapshot: Optional[Dict[str, Any]],
        before_url: str,
        snapshot: Dict[str, Any],
        next_step: Optional[WorkflowStep],
        transitioned: bool,
        dom_stable: bool,
        saw_busy_overlay: bool,
        saw_dom_change: bool,
        overlays_cleared: bool,
    ) -> Dict[str, bool]:
        try:
            current_url = page.url or ""
        except Exception:
            current_url = ""

        route_changed = bool(
            transitioned
            or (before_url and current_url and current_url != before_url)
            or self._snapshot_identity_changed(before_snapshot, snapshot)
        )
        modal_open = self._modal_or_dialog_visible(page)
        confirmation_visible = self._confirmation_panel_visible(page)
        success_toast_visible = self._success_toast_visible(page)
        content_changed = self._material_content_changed(before_snapshot, snapshot)

        spinner_settled = bool(
            overlays_cleared
            and (self._ajax_settled(page) or dom_stable)
            and (saw_busy_overlay or saw_dom_change or route_changed or content_changed)
        )
        new_controls_visible = bool(
            self._new_controls_visible(page)
            and (route_changed or content_changed or modal_open or success_toast_visible or saw_dom_change)
        )
        known_next_container = self._known_next_container_visible(page, next_step)

        next_step_actionable = False
        if next_step is not None:
            next_step_actionable, _ = self._is_step_actionable(page, next_step)

        return {
            "route_changed": route_changed,
            "modal_open": modal_open,
            "confirmation_visible": confirmation_visible,
            "content_changed": content_changed,
            "spinner_settled": spinner_settled,
            "new_controls_visible": new_controls_visible,
            "success_toast_visible": success_toast_visible,
            "known_next_container": known_next_container,
            "next_step_actionable": next_step_actionable,
        }

    @staticmethod
    def _navigation_outcome_is_blocked(signals: Dict[str, bool], transitioned: bool) -> bool:
        if any(
            signals.get(name, False)
            for name in (
                "route_changed",
                "modal_open",
                "confirmation_visible",
                "content_changed",
                "new_controls_visible",
                "success_toast_visible",
                "known_next_container",
            )
        ):
            return False

        if transitioned and signals.get("spinner_settled", False):
            return False

        return (not signals.get("spinner_settled", False)) and (not signals.get("next_step_actionable", False))

    @staticmethod
    def _format_navigation_signals(signals: Dict[str, bool]) -> str:
        enabled = [name for name, state in signals.items() if state]
        return ", ".join(enabled) if enabled else "none"

    def _suppress_stale_post_navigation_preface(
        self,
        page: Page,
        planned_steps: List[WorkflowStep],
        nav_index: int,
        baseline_snapshot: Optional[Dict[str, Any]],
    ) -> int:
        return self._suppress_stale_preface_after_index(
            page=page,
            planned_steps=planned_steps,
            after_index=nav_index,
            baseline_snapshot=baseline_snapshot,
        )

    def _suppress_stale_steps_after_context_change(
        self,
        page: Page,
        current_step: WorkflowStep,
        baseline_snapshot: Optional[Dict[str, Any]] = None,
    ) -> int:
        return self._suppress_stale_preface_after_index(
            page=page,
            planned_steps=self._steps,
            after_index=current_step.index,
            baseline_snapshot=baseline_snapshot,
        )

    def _suppress_stale_preface_after_index(
        self,
        page: Page,
        planned_steps: List[WorkflowStep],
        after_index: int,
        baseline_snapshot: Optional[Dict[str, Any]],
    ) -> int:
        snapshot = baseline_snapshot if isinstance(baseline_snapshot, dict) else {}
        if not snapshot:
            scan = self._scan_current_page_fields(page)
            if isinstance(scan, dict):
                snapshot = scan

        live_fields = snapshot.get("fields") if isinstance(snapshot, dict) else []
        live_fields = live_fields if isinstance(live_fields, list) else []
        live_key_set = self._field_key_set([field for field in live_fields if isinstance(field, dict)])

        stale_prefix: List[WorkflowStep] = []
        first_live: Optional[WorkflowStep] = None
        scanned = 0

        for idx in range(after_index + 1, len(planned_steps)):
            step = planned_steps[idx]
            if step.skip or step.executed:
                continue
            scanned += 1
            if scanned > 45:
                break
            if self._is_nav_step(step):
                break
            if step.step_type in (StepType.CLICK, StepType.CLICK_LINK) and not self._is_datepicker_day_click(step):
                if stale_prefix and self._is_context_anchor_step(page, step):
                    first_live = step
                break
            if not self._is_recordable_fill_step(step):
                continue

            if self._is_low_confidence_fill_step(step) or self._is_obsolete_context_step(page, step, snapshot):
                stale_prefix.append(step)
                continue

            present_on_page = self._field_matches_key_set(self._step_field_payload(step), live_key_set)
            actionable, _ = self._is_step_actionable(page, step)

            if present_on_page or actionable:
                first_live = step
                break

            stale_prefix.append(step)

        if first_live is None or not stale_prefix:
            return 0

        suppressed = 0
        for step in stale_prefix:
            if step.is_credential_field:
                continue
            step.skip = True
            suppressed += 1

        if suppressed:
            self._log(
                f"  suppressed {suppressed} stale post-transition step(s) before '{first_live.display_label}'",
                "WARNING",
            )

        return suppressed

    def _obsolete_context_skip_reason(self, page: Page, step: WorkflowStep) -> str:
        if not self._looks_like_previous_employee_context_step(step):
            return ""
        if self._is_obsolete_context_step(page, step):
            return "Obsolete page context; hidden previous-page field skipped"
        return ""

    def _is_obsolete_context_step(
        self,
        page: Page,
        step: WorkflowStep,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self._looks_like_previous_employee_context_step(step):
            return False
        if not self._plan_section_ready(page):
            return False
        actionable, _ = self._is_step_actionable(page, step)
        return not actionable

    @staticmethod
    def _looks_like_previous_employee_context_step(step: WorkflowStep) -> bool:
        if step.is_credential_field or step.step_type not in (StepType.INPUT, StepType.SELECT, StepType.TOGGLE, StepType.DATE):
            return False
        blob = " ".join([step.id, step.name, step.selector]).lower()
        return "employeevm_" in blob or "employeevm." in blob

    def _is_context_anchor_step(self, page: Page, step: WorkflowStep) -> bool:
        actionable, _ = self._is_step_actionable(page, step)
        if actionable:
            return True
        if self._is_plan_accordion_step(step) and self._plan_section_ready(page):
            return True
        if self._is_payment_step(step) and self._payment_context_visible(page):
            return True
        return False

    @staticmethod
    def _is_low_confidence_fill_step(step: WorkflowStep) -> bool:
        if step.id or step.name or step.label:
            return False
        selector = (step.selector or "").strip().lower()
        return selector in {"input", "select", "textarea"}

    @staticmethod
    def _modal_or_dialog_visible(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const selectors = [
                            '[role="dialog"]', '.modal.show', '.swal2-container.swal2-backdrop-show',
                            '.ui-dialog', '.bootbox.modal.show', '.k-window'
                        ];
                        const visible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };
                        return selectors.some((selector) => Array.from(document.querySelectorAll(selector)).some(visible));
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _confirmation_panel_visible(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const selectors = [
                            '.alert-success', '.validation-summary-valid', '.validation-summary-errors',
                            '.confirmation', '.confirm', '.success', '.message-success'
                        ];
                        const text = String(document.body ? document.body.innerText : '').toLowerCase();
                        const phrases = ['success', 'saved', 'submitted', 'completed', 'thank you', 'confirmation'];
                        const hasPhrase = phrases.some((phrase) => text.includes(phrase));
                        const visible = (el) => {
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };
                        const hasPanel = selectors.some((selector) => Array.from(document.querySelectorAll(selector)).some(visible));
                        return hasPanel || hasPhrase;
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _success_toast_visible(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const selectors = ['.toast.show', '.toast-success', '.notification-success', '.k-notification-success', '[role="status"]'];
                        const visible = (el) => {
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };
                        return selectors.some((selector) => Array.from(document.querySelectorAll(selector)).some(visible));
                    }"""
                )
            )
        except Exception:
            return False

    @staticmethod
    def _new_controls_visible(page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const controls = Array.from(document.querySelectorAll('button, a, input[type="submit"], input[type="button"]'));
                        const visible = (el) => {
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || '1') === 0) return false;
                            return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                        };
                        const words = ['next', 'submit', 'continue', 'save', 'close', 'ok', 'done', 'confirm'];
                        return controls.some((el) => {
                            if (!visible(el)) return false;
                            if (el.disabled) return false;
                            const text = String(el.innerText || el.textContent || el.value || '').toLowerCase();
                            return words.some((w) => text.includes(w));
                        });
                    }"""
                )
            )
        except Exception:
            return False

    def _known_next_container_visible(self, page: Page, next_step: Optional[WorkflowStep]) -> bool:
        selectors = [
            "#BtnSubmitEnrollment",
            "#BtnGotoProfile",
            "a.planAccordian",
            "[id*='ProductLst']",
            "[name*='ProductLst']",
            "#rd_no",
            "[name='rd_payment']",
            "#DropDownPlanYear",
            "[role='dialog']",
            ".modal.show",
            ".alert-success",
        ]

        if next_step and next_step.id:
            safe = next_step.id.replace("[", "\\[").replace("]", "\\]")
            selectors.append(f"#{safe}")
        if next_step and next_step.selector:
            selectors.append(next_step.selector)

        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    def _plan_section_ready(self, page: Page) -> bool:
        return InputService._effective_controls_loaded(page)

    def _wait_for_plan_section_ready(self, page: Page, timeout_s: float = 20.0) -> bool:
        deadline = time.time() + max(timeout_s, 0.5)
        while time.time() < deadline:
            if self._plan_section_ready(page) and self._loading_overlays_cleared(page) and self._ajax_settled(page):
                return True
            time.sleep(0.25)
        return self._plan_section_ready(page)

    def _product_choice_ready(self, page: Page, step: WorkflowStep) -> bool:
        value = str(step.value or "").strip()
        selectors: List[str] = []
        if value:
            safe_value = value.replace("'", "\\'")
            selectors.extend([
                f"input[type='radio'][value='{safe_value}']",
                f"input[type='radio'][name*='ProductLst'][value='{safe_value}']",
            ])
        selectors.extend([
            "input[type='radio'][name*='ProductLst'][name*='SelectedLineId']",
            "input[type='radio'][id*='ProductLst'][id*='SelectedLineId']",
            "input[type='checkbox'][name*='ProductLst'][name*='IsDeclinedInd']",
            "input[type='checkbox'][id*='ProductLst'][id*='IsDeclinedInd']",
        ])

        for selector in selectors:
            try:
                if page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue

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
                        return Array.from(document.querySelectorAll('label, button, a, [role="button"]')).some((el) => {
                            return visible(el) && /\bselect\s+plan\b/.test(txt(el.innerText || el.textContent || el.value || el.getAttribute('aria-label')));
                        });
                    }"""
                )
            )
        except Exception:
            return False

    def _wait_for_product_choice_ready(self, page: Page, step: WorkflowStep, timeout_s: float = 15.0) -> bool:
        deadline = time.time() + max(timeout_s, 0.5)
        while time.time() < deadline:
            if self._product_choice_ready(page, step) and self._loading_overlays_cleared(page):
                return True
            time.sleep(0.25)
        return self._product_choice_ready(page, step)

    @staticmethod
    def _is_plan_accordion_step(step: WorkflowStep) -> bool:
        if step.step_type not in (StepType.CLICK, StepType.CLICK_LINK):
            return False
        blob = " ".join([step.text, step.label, step.selector, step.id, step.name]).lower()
        return "planaccordian" in blob or bool(re.search(r"\bplans?\b", blob))

    @staticmethod
    def _is_product_plan_step(step: WorkflowStep) -> bool:
        blob = " ".join([step.id, step.name, step.label, step.text, step.selector]).lower()
        return "productlst" in blob or "select plan" in blob

    @staticmethod
    def _is_product_decline_step(step: WorkflowStep) -> bool:
        blob = " ".join([step.id, step.name, step.label, step.text, step.selector]).lower()
        return "productlst" in blob and "isdeclinedind" in blob

    @staticmethod
    def _is_payment_step(step: WorkflowStep) -> bool:
        blob = " ".join([step.id, step.name, step.label, step.text, step.selector]).lower()
        return "rd_payment" in blob or "payment" in blob or step.id in {"rd_no", "rd_yes"}

    @staticmethod
    def _is_submit_enrollment_step(step: WorkflowStep) -> bool:
        blob = " ".join([step.id, step.name, step.label, step.text, step.selector, step.input_type]).lower()
        return "btnsubmitenrollment" in blob or ("submit" in blob and "login" not in blob)

    def _payment_context_visible(self, page: Page) -> bool:
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
                        if (/\b(payment method|payment|submit enrollment)\b/.test(bodyText)) return true;
                        return Array.from(document.querySelectorAll('input, label, button, a')).some((el) => {
                            const blob = `${txt(el.id)} ${txt(el.name)} ${txt(el.innerText || el.textContent || el.value || el.getAttribute('aria-label'))}`;
                            return /(rd_payment|rd_no|rd_yes|payment|submit)/.test(blob) && visible(el);
                        });
                    }"""
                )
            )
        except Exception:
            return False

    def _wait_for_payment_section_ready(self, page: Page, timeout_s: float = 12.0) -> bool:
        deadline = time.time() + max(timeout_s, 0.5)
        while time.time() < deadline:
            if self._payment_context_visible(page) and self._loading_overlays_cleared(page):
                return True
            time.sleep(0.25)
        return self._payment_context_visible(page)

    def _click_plan_accordion_by_text(self, page: Page, step: WorkflowStep) -> bool:
        target = re.sub(r"\s+", " ", step.text or step.label or "").strip()
        if not target:
            return False

        self._wait_for_plan_section_ready(page, timeout_s=20.0)
        locators = []
        try:
            locators.append(page.locator("a.planAccordian").filter(has_text=target))
        except Exception:
            pass
        for selector in ("a", "button", "[role='button']", ".accordion a", ".panel-title a"):
            try:
                locators.append(page.locator(selector).filter(has_text=target))
            except Exception:
                continue

        for loc in locators:
            try:
                count = min(loc.count(), 10)
            except Exception:
                continue
            for idx in range(count):
                candidate = loc.nth(idx)
                try:
                    if not candidate.is_visible(timeout=500):
                        continue
                    candidate.scroll_into_view_if_needed(timeout=2_000)
                    candidate.click(timeout=5_000, no_wait_after=True)
                    NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                    return True
                except Exception:
                    continue
        return False

    def _click_dynamic_plan_choice(self, page: Page, step: WorkflowStep) -> bool:
        self._wait_for_product_choice_ready(page, step, timeout_s=20.0)
        value = str(step.value or "").strip()
        selectors: List[str] = []
        if value:
            safe_value = value.replace("'", "\\'")
            selectors.extend([
                f"input[type='radio'][value='{safe_value}']",
                f"input[type='radio'][name*='ProductLst'][value='{safe_value}']",
            ])
        selectors.extend([
            "input[type='radio'][name*='ProductLst'][name*='SelectedLineId']",
            "input[type='radio'][id*='ProductLst'][id*='SelectedLineId']",
        ])

        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0:
                    continue
                loc.check(timeout=3_000, force=True)
                NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                return True
            except Exception:
                continue

        for selector in ("label", "button", "a", "[role='button']"):
            try:
                loc = page.locator(selector).filter(has_text=re.compile(r"select\s+plan", re.I)).first
                if loc.count() == 0 or not loc.is_visible(timeout=500):
                    continue
                loc.scroll_into_view_if_needed(timeout=2_000)
                loc.click(timeout=5_000, no_wait_after=True)
                NavigationService.wait_ajax(page, timeout_ms=self._AJAX_TIMEOUT)
                return True
            except Exception:
                continue
        return False

    def _click_visible_action_by_text(self, page: Page, step: WorkflowStep) -> bool:
        target = re.sub(r"\s+", " ", step.text or step.label or step.value or "").strip()
        if not target:
            return False
        pattern = re.compile(rf"^\s*{re.escape(target)}\s*$", re.I)
        role_locators = []
        for role in ("button", "link"):
            try:
                role_locators.append(page.get_by_role(role, name=pattern))
            except Exception:
                continue
        for loc in role_locators:
            try:
                count = min(loc.count(), 10)
            except Exception:
                continue
            for idx in range(count):
                candidate = loc.nth(idx)
                try:
                    if not candidate.is_visible(timeout=500):
                        continue
                    candidate.scroll_into_view_if_needed(timeout=2_000)
                    candidate.click(timeout=5_000, no_wait_after=False)
                    return True
                except Exception:
                    continue

        for selector in ("button", "a", "input[type='submit']", "input[type='button']", "[role='button']"):
            try:
                loc = page.locator(selector).filter(has_text=pattern)
            except Exception:
                continue
            try:
                count = min(loc.count(), 10)
            except Exception:
                continue
            for idx in range(count):
                candidate = loc.nth(idx)
                try:
                    if not candidate.is_visible(timeout=500):
                        continue
                    candidate.scroll_into_view_if_needed(timeout=2_000)
                    candidate.click(timeout=5_000, no_wait_after=False)
                    return True
                except Exception:
                    continue
        return False

    def _material_content_changed(
        self,
        before_snapshot: Optional[Dict[str, Any]],
        after_snapshot: Optional[Dict[str, Any]],
    ) -> bool:
        if self._snapshot_identity_changed(before_snapshot, after_snapshot):
            return True

        before = before_snapshot or {}
        after = after_snapshot or {}
        if not before or not after:
            return False

        before_count = int(before.get("field_count") or 0)
        after_count = int(after.get("field_count") or 0)
        return bool(before_count and after_count and abs(after_count - before_count) >= 2)

    @staticmethod
    def _loading_overlays_cleared(page: Page) -> bool:
        return InputService._loading_overlays_cleared(page)

    @staticmethod
    def _ajax_settled(page: Page) -> bool:
        return InputService._ajax_settled(page)

    def _wait_for_post_navigation_transition(
        self,
        page: Page,
        before_snapshot: Optional[Dict[str, Any]],
        before_url: str,
        timeout_s: float,
    ) -> Tuple[bool, Dict[str, Any]]:
        deadline = time.time() + max(timeout_s, 0.5)
        last_snapshot = self._scan_current_page_fields(page)

        while time.time() < deadline:
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""

            snapshot = self._scan_current_page_fields(page)
            if isinstance(snapshot, dict) and snapshot:
                last_snapshot = snapshot

            if before_url and current_url and current_url != before_url:
                return True, last_snapshot

            if self._snapshot_identity_changed(before_snapshot, snapshot):
                return True, last_snapshot

            time.sleep(0.2)

        return False, last_snapshot if isinstance(last_snapshot, dict) else {}

    def _snapshot_identity_changed(
        self,
        before: Optional[Dict[str, Any]],
        after: Optional[Dict[str, Any]],
    ) -> bool:
        before = before or {}
        after = after or {}
        if not before or not after:
            return False

        before_signature = self._norm(before.get("signature"))
        after_signature = self._norm(after.get("signature"))
        if before_signature and after_signature and before_signature != after_signature:
            return True

        before_url = self._norm(before.get("url"))
        after_url = self._norm(after.get("url"))
        if before_url and after_url and before_url != after_url:
            return True

        before_path = self._norm(before.get("path"))
        after_path = self._norm(after.get("path"))
        if before_path and after_path and before_path != after_path:
            return True

        before_page_id = self._norm(before.get("page_id"))
        after_page_id = self._norm(after.get("page_id"))
        if before_page_id and after_page_id and before_page_id != after_page_id:
            return True

        before_title = self._norm(before.get("title"))
        after_title = self._norm(after.get("title"))
        return bool(before_title and after_title and before_title != after_title)

    def _next_executable_field_step(
        self,
        planned_steps: List[WorkflowStep],
        nav_index: int,
    ) -> Tuple[Optional[int], Optional[WorkflowStep]]:
        for idx in range(nav_index + 1, len(planned_steps)):
            step = planned_steps[idx]
            if step.skip or step.executed:
                continue
            if self._is_nav_step(step):
                break
            if self._is_recordable_fill_step(step):
                return idx, step
        return None, None

    def _prioritize_required_gate_step(
        self,
        page: Page,
        planned_steps: List[WorkflowStep],
        nav_index: int,
    ) -> Optional[int]:
        required_fields = self._scan_required_gate_fields(page)
        if not required_fields:
            return None

        chosen_idx: Optional[int] = None
        chosen_score: Optional[Tuple[int, int, int]] = None

        for idx in range(nav_index + 1, len(planned_steps)):
            step = planned_steps[idx]
            if step.skip or step.executed:
                continue
            if idx > nav_index + 1 and self._is_nav_step(step):
                break
            if not self._is_recordable_fill_step(step):
                continue

            field_meta = self._required_field_match(step, required_fields)
            if field_meta is None:
                continue

            visibility_rank = 0 if field_meta.get("visible") else 1
            gate_rank = 0 if self._is_gate_field_step(step) else 1
            score = (visibility_rank, gate_rank, idx)

            if chosen_score is None or score < chosen_score:
                chosen_idx = idx
                chosen_score = score

        if chosen_idx is None:
            return None
        if chosen_idx == nav_index + 1:
            return chosen_idx

        gate_step = planned_steps.pop(chosen_idx)
        planned_steps.insert(nav_index + 1, gate_step)
        return nav_index + 1

    def _scan_required_gate_fields(self, page: Page) -> List[Dict[str, Any]]:
        try:
            fields = page.evaluate(
                r"""() => {
                    function txt(v) {
                        return String(v || '').replace(/\s+/g, ' ').trim();
                    }

                    function getLabel(el) {
                        if (el.labels && el.labels[0]) return txt(el.labels[0].innerText || el.labels[0].textContent);
                        var aria = el.getAttribute('aria-label');
                        if (aria) return txt(aria);
                        if (el.placeholder) return txt(el.placeholder);
                        var prev = el.previousElementSibling;
                        if (prev) return txt(prev.innerText || prev.textContent);
                        return '';
                    }

                    function isVisible(el) {
                        var style = window.getComputedStyle(el);
                        return !!(el.offsetWidth || el.offsetHeight)
                            && style.visibility !== 'hidden'
                            && style.display !== 'none';
                    }

                    return Array.from(document.querySelectorAll('input, textarea, select'))
                        .map(function(el) {
                            var required = !!(el.required || el.getAttribute('aria-required') === 'true');
                            if (!required) return null;
                            return {
                                id: el.id || '',
                                name: el.name || '',
                                label: getLabel(el),
                                placeholder: el.placeholder || '',
                                visible: isVisible(el),
                            };
                        })
                        .filter(Boolean);
                }"""
            ) or []
        except Exception:
            return []

        if not isinstance(fields, list):
            return []
        return [f for f in fields if isinstance(f, dict)]

    def _required_field_match(
        self,
        step: WorkflowStep,
        required_fields: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        step_id = self._norm(step.id)
        step_name = self._norm(step.name)
        step_tokens = {
            self._norm(step.label),
            self._norm(step.text),
            self._norm(step.placeholder),
        }
        step_tokens.discard("")

        for field in required_fields:
            field_id = self._norm(field.get("id"))
            field_name = self._norm(field.get("name"))
            field_tokens = {
                self._norm(field.get("label")),
                self._norm(field.get("placeholder")),
            }
            field_tokens.discard("")

            if step_id and field_id and step_id == field_id:
                return field
            if step_name and field_name and step_name == field_name:
                return field
            if step_tokens and field_tokens and not step_tokens.isdisjoint(field_tokens):
                return field

        return None

    def _is_gate_field_step(self, step: WorkflowStep) -> bool:
        blob = self._norm(" ".join([step.id, step.name, step.label, step.text, step.placeholder]))
        return any(token in blob for token in ("effective", "enrollmentwindow", "window start", "window end"))

    def _wait_for_step_actionable(
        self,
        page: Page,
        step: WorkflowStep,
        timeout_s: float,
    ) -> Tuple[bool, str]:
        deadline = time.time() + max(timeout_s, 0.5)
        detail = ""

        while time.time() < deadline:
            ok, detail = self._is_step_actionable(page, step)
            if ok:
                return True, ""
            time.sleep(0.2)

        return False, detail or "element remained hidden or disabled"

    def _is_step_actionable(self, page: Page, step: WorkflowStep) -> Tuple[bool, str]:
        locator = self._find_visible_step_locator(page, step)
        if locator is not None:
            try:
                if locator.is_disabled(timeout=300):
                    return False, "element is disabled"
            except Exception:
                pass
            return True, ""

        if step.step_type == StepType.SELECT and self._has_visible_chosen_widget(page, step):
            return True, ""

        return False, "element is not visible"

    def _find_visible_step_locator(self, page: Page, step: WorkflowStep) -> Optional[Locator]:
        selectors = self._loc._strategies(step)
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = loc.count()
            except Exception:
                continue

            for idx in range(min(count, 12)):
                candidate = loc.nth(idx)
                try:
                    if candidate.is_visible(timeout=300):
                        return candidate
                except Exception:
                    continue
        return None

    def _has_visible_chosen_widget(self, page: Page, step: WorkflowStep) -> bool:
        if not step.id:
            return False

        selectors = (
            f"#{step.id}_chzn",
            f"#{step.id}_chosen",
            f"[id='{step.id}_chzn']",
            f"[id='{step.id}_chosen']",
        )
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        return False

    def _password_coming(self, step: WorkflowStep) -> bool:
        return self._auth_service._password_coming(step)

    def _login_click_ahead(self, step: WorkflowStep) -> bool:
        return self._auth_service._login_click_ahead(step)

    def _mark_login_steps_done(self, after: WorkflowStep) -> None:
        self._auth_service._mark_login_steps_done(after)

    def _record(self, step: WorkflowStep, sr: StepResult) -> None:
        self._result.step_results.append(sr)
        self._pairs.append((step, sr))
        self._record_runtime_field(step, sr)
        self._track_recorded_field_not_filled(step, sr)
        self._remember_fill_expectation(step, sr)
        self._remember_expected_page_field(step, sr)

    def _record_runtime_field(self, step: WorkflowStep, sr: StepResult) -> None:
        if step.is_credential_field:
            return

        if self._is_plan_accordion_step(step) and sr.success:
            self._runtime_plan_context = re.sub(r"\s+", " ", step.text or step.label or "").strip()
            return

        value = self._runtime_value_for_step(step, sr)
        if not value or not self._should_capture_runtime_field(step, sr, value):
            return

        self._runtime_fields.append({
            "step_index": step.index + 1,
            "type": step.type,
            "label": step.display_label,
            "id": step.id,
            "name": step.name,
            "text": step.text,
            "input_type": step.input_type,
            "value": value,
            "status": "SKIP" if sr.skipped else "PASS" if sr.success else "FAIL",
            "message": sr.message,
            "category": self._runtime_category_for_step(step),
            "plan_context": self._runtime_plan_context if self._is_product_plan_step(step) or self._is_product_decline_step(step) else "",
        })

    def _runtime_value_for_step(self, step: WorkflowStep, sr: StepResult) -> str:
        value = str(sr.faker_value or "").strip()
        if value:
            return value

        if step.step_type in (StepType.SELECT, StepType.TOGGLE):
            return str(step.text or step.label or step.value or "").strip()
        if step.step_type in (StepType.INPUT, StepType.DATE):
            return str(step.value or "").strip()

        input_type = (step.input_type or "").lower()
        if input_type in {"radio", "checkbox"}:
            if self._is_product_decline_step(step):
                wanted = (step.value or "").lower() in {"true", "yes", "1", "on"}
                return "Declined" if wanted else "Not declined"
            return str(step.text or step.label or step.value or "").strip()
        return ""

    def _should_capture_runtime_field(self, step: WorkflowStep, sr: StepResult, value: str) -> bool:
        if not value:
            return False
        if step.step_type in (StepType.INPUT, StepType.DATE, StepType.SELECT, StepType.TOGGLE):
            return True
        input_type = (step.input_type or "").lower()
        return input_type in {"radio", "checkbox"} or self._is_product_plan_step(step) or self._is_payment_step(step)

    def _runtime_category_for_step(self, step: WorkflowStep) -> str:
        if self._is_product_plan_step(step) and "selectedlineid" in self._norm(" ".join([step.id, step.name, step.selector])):
            return "plan_selection"
        if self._is_product_decline_step(step):
            return "plan_decline"
        if self._is_payment_step(step):
            return "payment"
        if step.step_type == StepType.SELECT:
            return "select"
        if step.step_type in (StepType.INPUT, StepType.DATE):
            return "input"
        if step.step_type == StepType.TOGGLE:
            return "toggle"
        return "action"

    def _is_runtime_action_data_step(self, step: WorkflowStep) -> bool:
        input_type = (step.input_type or "").lower()
        return input_type in {"radio", "checkbox"} or self._is_product_plan_step(step) or self._is_payment_step(step)

    def _runtime_action_value(self, page: Page, step: WorkflowStep, locator: Optional[Locator] = None) -> str:
        if self._is_product_decline_step(step):
            checked = self._locator_checked_state(locator)
            if checked is not None:
                return "Declined" if checked else "Not declined"
            wanted = (step.value or "").lower() in {"true", "yes", "1", "on"}
            return "Declined" if wanted else "Not declined"

        if self._is_product_plan_step(step):
            return self._selected_product_plan_text(page, step, locator)

        label = self._control_label_text(locator) if locator is not None else ""
        return label or str(step.text or step.label or step.value or "").strip()

    def _selected_product_plan_text(self, page: Page, step: WorkflowStep, locator: Optional[Locator] = None) -> str:
        for candidate in (locator, self._checked_product_locator(page, step)):
            if candidate is None:
                continue
            text = self._nearby_control_text(candidate)
            if text:
                return text
        return str(step.text or step.label or step.value or "").strip()

    def _checked_product_locator(self, page: Page, step: WorkflowStep) -> Optional[Locator]:
        selectors: List[str] = []
        if step.value:
            safe_value = str(step.value).replace("'", "\\'")
            selectors.append(f"input[type='radio'][name*='ProductLst'][value='{safe_value}']:checked")
        selectors.append("input[type='radio'][name*='ProductLst'][name*='SelectedLineId']:checked")
        selectors.append("input[type='radio'][id*='ProductLst'][id*='SelectedLineId']:checked")
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if locator.count() > 0:
                    return locator
            except Exception:
                continue
        return None

    def _nearby_control_text(self, locator: Locator) -> str:
        try:
            text = locator.evaluate(
                r"""(element) => {
                    const clean = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const label = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
                    if (label && clean(label.innerText || label.textContent)) return clean(label.innerText || label.textContent);
                    const container = element.closest('tr, li, .row, .panel, .card, .plan, .form-group, div') || element.parentElement;
                    if (container && clean(container.innerText || container.textContent)) return clean(container.innerText || container.textContent);
                    return clean(element.getAttribute('aria-label') || element.value || element.name || element.id);
                }"""
            )
            return re.sub(r"\s+", " ", str(text or "")).strip()[:500]
        except Exception:
            return ""

    def _control_label_text(self, locator: Optional[Locator]) -> str:
        if locator is None:
            return ""
        return self._nearby_control_text(locator)

    @staticmethod
    def _locator_checked_state(locator: Optional[Locator]) -> Optional[bool]:
        if locator is None:
            return None
        try:
            return bool(locator.is_checked(timeout=300))
        except Exception:
            return None

    def _remember_expected_page_field(self, step: WorkflowStep, sr: StepResult) -> None:
        self._discrepancy_service._remember_expected_page_field(step, sr)

    def _should_track_expected_page_field(self, step: WorkflowStep, sr: StepResult) -> bool:
        return self._discrepancy_service._should_track_expected_page_field(step, sr)

    def _remember_fill_expectation(self, step: WorkflowStep, sr: StepResult) -> None:
        self._discrepancy_service._remember_fill_expectation(step, sr)

    def _should_verify_filled_step(self, step: WorkflowStep, sr: StepResult) -> bool:
        return self._discrepancy_service._should_verify_filled_step(step, sr)

    def _expected_filled_value(self, step: WorkflowStep, sr: StepResult) -> str:
        return self._discrepancy_service._expected_filled_value(step, sr)

    def _pending_fill_verification_key(self, step: WorkflowStep) -> str:
        return self._discrepancy_service._pending_fill_verification_key(step)

    def _verify_pending_fill_states(self, page: Page, trigger: str = "") -> None:
        self._discrepancy_service._verify_pending_fill_states(page, trigger)

    def _filled_state_matches_current_page(
        self,
        page: Page,
        step: WorkflowStep,
        expected_value: str,
    ) -> Tuple[bool, str]:
        return self._discrepancy_service._filled_state_matches_current_page(page, step, expected_value)

    def _clear_pending_fill_states_if_page_changed(
        self,
        before_snapshot: Optional[Dict[str, Any]],
        after_snapshot: Optional[Dict[str, Any]],
    ) -> None:
        self._discrepancy_service._clear_pending_fill_states_if_page_changed(before_snapshot, after_snapshot)

    def _record_healed_selector(
        self,
        page: Page,
        step: WorkflowStep,
        strategy: str,
        confidence: float,
    ) -> None:
        self._discrepancy_service._record_healed_selector(page, step, strategy, confidence)

    def _track_recorded_field_not_filled(self, step: WorkflowStep, sr: StepResult) -> None:
        self._discrepancy_service._track_recorded_field_not_filled(step, sr)

    @staticmethod
    def _is_recordable_fill_step(step: WorkflowStep) -> bool:
        return DiscrepancyService._is_recordable_fill_step(step)

    def _is_intentional_fill_skip(self, step: WorkflowStep, sr: StepResult) -> bool:
        return self._discrepancy_service._is_intentional_fill_skip(step, sr)

    @staticmethod
    def _step_field_payload(step: WorkflowStep) -> Dict[str, Any]:
        return DiscrepancyService._step_field_payload(step)

    def _collect_page_discrepancies(self, page: Page, trigger: str = "") -> None:
        self._discrepancy_service._collect_page_discrepancies(page, trigger)

    def _scan_current_page_fields(self, page: Page) -> Dict[str, Any]:
        return self._discrepancy_service._scan_current_page_fields(page)

    def _select_expected_checkpoint(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._discrepancy_service._select_expected_checkpoint(snapshot)

    def _compare_checkpoint_fields(
        self,
        checkpoint: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Tuple[Dict[str, Any], Dict[str, Any]]], List[Dict[str, Any]]]:
        return self._discrepancy_service._compare_checkpoint_fields(checkpoint, snapshot)

    def _append_discrepancy(
        self,
        kind: str,
        live_snapshot: Dict[str, Any],
        field: Dict[str, Any],
        message: str,
        dedupe_key_extra: str = "",
        screenshot_path: Optional[str] = None,
        expected_field: Optional[Dict[str, Any]] = None,
        live_field: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._discrepancy_service._append_discrepancy(
            kind=kind,
            live_snapshot=live_snapshot,
            field=field,
            message=message,
            dedupe_key_extra=dedupe_key_extra,
            screenshot_path=screenshot_path,
            expected_field=expected_field,
            live_field=live_field,
        )

    def _qa_discrepancy_screenshot(
        self,
        page: Page,
        trigger: str,
        missing_count: int,
        new_count: int,
        renamed_count: int = 0,
    ) -> Optional[str]:
        return self._discrepancy_service._qa_discrepancy_screenshot(page, trigger, missing_count, new_count, renamed_count)

    def _resolve_data_generation_config(self) -> Tuple[Optional[int], str, bool, str, str, float]:
        profile = self._cfg.execution_profile if isinstance(self._cfg.execution_profile, dict) else {}
        local_cfg = self._load_playback_config_properties()

        seed: Optional[int] = None
        seed_raw = (
            profile.get("data_seed")
            or profile.get("replay_seed")
            or profile.get("seed")
            or local_cfg.get("data_seed")
            or local_cfg.get("replay_seed")
            or os.environ.get("REPLAY_DATA_SEED")
        )
        if seed_raw not in (None, ""):
            try:
                seed = int(str(seed_raw).strip())
            except Exception:
                seed = None

        fill_order_mode = str(
            profile.get("fill_order_mode")
            or profile.get("replay_fill_order")
            or local_cfg.get("fill_order_mode")
            or local_cfg.get("replay_fill_order")
            or "recorded"
        ).strip().lower()
        if fill_order_mode not in {"recorded", "dependency"}:
            fill_order_mode = "recorded"
        if self._as_bool(
            profile.get("dependency_fill_order")
            if profile.get("dependency_fill_order") is not None
            else local_cfg.get("dependency_fill_order"),
            default=False,
        ):
            fill_order_mode = "dependency"

        use_ollama_raw = profile.get("use_ollama_data")
        if use_ollama_raw is None:
            use_ollama_raw = profile.get("ollama_enabled")
        if use_ollama_raw is None:
            use_ollama_raw = local_cfg.get("use_ollama_data")
        if use_ollama_raw is None:
            use_ollama_raw = local_cfg.get("ollama_enabled")

        use_ollama = self._as_bool(
            use_ollama_raw,
            default=self._as_bool(os.environ.get("REPLAY_USE_OLLAMA"), default=False),
        )

        ollama_model = str(
            profile.get("ollama_model")
            or profile.get("_ollama_model")
            or local_cfg.get("ollama_model")
            or os.environ.get("OLLAMA_MODEL")
            or ""
        ).strip()
        ollama_url = str(
            profile.get("ollama_url")
            or profile.get("_ollama_url")
            or local_cfg.get("ollama_url")
            or os.environ.get("OLLAMA_URL")
            or "http://127.0.0.1:11434/api/generate"
        ).strip()

        timeout_raw = (
            profile.get("ollama_timeout_s")
            or profile.get("ollama_timeout")
            or local_cfg.get("ollama_timeout_s")
            or local_cfg.get("ollama_timeout")
            or os.environ.get("OLLAMA_TIMEOUT_S")
            or 8.0
        )
        try:
            ollama_timeout = max(1.0, float(timeout_raw))
        except Exception:
            ollama_timeout = 8.0

        return seed, fill_order_mode, use_ollama, ollama_model, ollama_url, ollama_timeout

    def _load_playback_config_properties(self) -> Dict[str, str]:
        """Load optional [playback] settings from config.properties."""
        config_path = os.path.join(os.getcwd(), "config.properties")
        if not os.path.exists(config_path):
            return {}

        out: Dict[str, str] = {}
        in_playback = False
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[") and line.endswith("]"):
                        in_playback = line[1:-1].strip().lower() == "playback"
                        continue
                    if not in_playback or "=" not in line:
                        continue
                    key, value = [part.strip() for part in line.split("=", 1)]
                    if key:
                        out[key.lower()] = value
        except Exception:
            return {}

        return out

    def _resolve_replay_policy(self) -> Tuple[str, bool, bool, bool]:
        """Resolve replay mode and fail-flags from execution profile with safe defaults."""
        profile = self._cfg.execution_profile if isinstance(self._cfg.execution_profile, dict) else {}

        mode = str(
            profile.get("_replay_mode")
            or profile.get("replay_mode")
            or self._cfg.replay_mode
            or "standard"
        ).strip().lower()
        if mode not in {"lenient", "standard", "strict"}:
            mode = "standard"

        fail_missing_required = self._as_bool(
            profile.get("_fail_on_missing_required_fields"),
            default=self._cfg.fail_on_missing_required_fields,
        )
        fail_new_required = self._as_bool(
            profile.get("_fail_on_new_required_fields"),
            default=self._cfg.fail_on_new_required_fields,
        )
        fail_not_filled = self._as_bool(
            profile.get("_fail_on_not_filled_fields"),
            default=self._cfg.fail_on_not_filled_fields,
        )

        return mode, fail_missing_required, fail_new_required, fail_not_filled

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)

        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)

    @staticmethod
    def _norm(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    def _has_identity(self, field: Dict[str, Any]) -> bool:
        return self._discrepancy_service._has_identity(field)

    def _field_key_set(self, fields: List[Dict[str, Any]]) -> Set[str]:
        return self._discrepancy_service._field_key_set(fields)

    def _field_matches_key_set(self, field: Dict[str, Any], key_set: Set[str]) -> bool:
        return self._discrepancy_service._field_matches_key_set(field, key_set)

    def _field_keys(self, field: Dict[str, Any]) -> List[str]:
        return self._discrepancy_service._field_keys(field)

    def _discrepancy_identity(self, field: Dict[str, Any]) -> str:
        return self._discrepancy_service._discrepancy_identity(field)

    def _is_page_alive(self, page: Page) -> bool:
        try:
            _ = page.url
            return True
        except Exception:
            return False

    def _log(self, message: str, level: str = "SYSTEM") -> None:
        logger.info(message)
        if self._cfg.update_callback:
            try:
                self._cfg.update_callback(message, level)
            except TypeError:
                self._cfg.update_callback(message)


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------

def _parse_steps(workflow_data: Dict[str, Any]) -> List[WorkflowStep]:
    return [WorkflowStep.from_dict(r, i) for i, r in enumerate(workflow_data.get("steps", []))]


def _parse_page_checkpoints(workflow_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = workflow_data.get("page_checkpoints", [])
    if not isinstance(raw, list):
        return []

    parsed: List[Dict[str, Any]] = []
    for checkpoint in raw:
        if not isinstance(checkpoint, dict):
            continue
        fields = checkpoint.get("fields", [])
        if not isinstance(fields, list):
            fields = []

        parsed.append({
            "page_id": str(checkpoint.get("page_id", "") or ""),
            "url": str(checkpoint.get("url", "") or ""),
            "path": str(checkpoint.get("path", "") or ""),
            "title": str(checkpoint.get("title", "") or ""),
            "signature": str(checkpoint.get("signature", "") or ""),
            "fields": [field for field in fields if isinstance(field, dict)],
        })

    return parsed
