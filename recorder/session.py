"""
recorder.session
-----------------
Manages a single browser recording session.

Opens a browser, injects the capture script, polls for user-triggered events
until the stop signal fires, normalises the captured steps and returns the
result.  No page scanning — only events the user actually triggers are kept.
"""
from __future__ import annotations

import logging
import time
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from .models import RecordedStep, RecordingResult, StepType
from .script import RecorderScript

logger = logging.getLogger(__name__)

# Noise container IDs: clicks on these are meaningless wrapper elements
_NOISE_IDS = frozenset({
    "employeeaddiv",
    "divsidemenuouter",
    "div_enrollmentsummarydet",
    "div_accodianhead_1",
    "div_showplans_1",
    "divcobselect",
    "divtotalbtn",
    "enrollsummarywrap",
    # Login page wrapper (credentials auto-filled by playback)
    "login",
    # jQuery UI datepicker overlay — transient, not a real form field
    "ui-datepicker-div",
})


class RecordingSession:
    """
    Manages a single recording session.

    Usage::

        session = RecordingSession(url, workflow_name, headless, stop_event)
        result  = session.start()   # blocks until stop_event fires
        steps   = result.steps      # list[RecordedStep]
    """

    POLL_INTERVAL: float = 0.2  # seconds between event drains
    CHECKPOINT_INTERVAL: float = 1.0  # seconds between page checkpoint probes

    def __init__(
        self,
        url: str,
        workflow_name: str = "workflow.json",
        headless: bool = False,
        stop_event: Optional[threading.Event] = None,
        update_callback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self._url = url
        self._workflow_name = workflow_name
        self._headless = headless
        self._stop_event: threading.Event = stop_event or threading.Event()
        self._update_callback = update_callback

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> RecordingResult:
        """
        Open browser, navigate to URL, capture interactions until stop_event
        fires, and return deduplicated steps.

        Uses context.expose_function + context.add_init_script so that events
        are pushed directly to Python in real-time.  This means navigation-
        triggering clicks (group link, Employee Administration, Add Employee,
        etc.) are captured BEFORE the page unloads — no polling race condition.

        Returns:
            RecordingResult containing all deduplicated RecordedStep objects.
        """
        self._stop_event.clear()
        start_time = time.time()
        raw_steps: List[Dict[str, Any]] = []
        page_checkpoints: List[Dict[str, Any]] = []

        pw = browser = page = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=self._headless)
            context = browser.new_context()

            # Expose a Python callable to JS — persists across page navigations
            def _push(ev: Dict[str, Any]) -> None:
                self._capture_event(raw_steps, ev, source="live")

            context.expose_function("__recorderPush", _push)

            # Auto-inject the recorder on EVERY page (including post-navigation pages)
            # Wrap in IIFE because add_init_script expects a plain script, not a function
            context.add_init_script(f"({RecorderScript.INJECT_JS})()")

            page = context.new_page()
        except Exception as exc:
            logger.error(f"[Recorder] Browser launch failed: {exc}")
            return RecordingResult(url=self._url, workflow_name=self._workflow_name)

        try:
            logger.info(f"[Recorder] Navigating to: {self._url}")
            page.goto(self._url, timeout=60_000, wait_until="domcontentloaded")
            self._wait_ready(page)
            self._capture_checkpoint(page, page_checkpoints, reason="initial")
            logger.info("[Recorder] Recording started — interact with the page")

            next_checkpoint_at = time.time() + self.CHECKPOINT_INTERVAL

            while not self._stop_event.is_set():
                try:
                    # Drain any fallback events (e.g. if __recorderPush wasn't
                    # ready on a very fast early page load)
                    for event in self._drain(page):
                        self._capture_event(raw_steps, event, source="fallback")
                except Exception as exc:
                    logger.warning(f"[Recorder] Poll error: {exc}")

                if time.time() >= next_checkpoint_at:
                    self._capture_checkpoint(page, page_checkpoints, reason="poll")
                    next_checkpoint_at = time.time() + self.CHECKPOINT_INTERVAL

                time.sleep(self.POLL_INTERVAL)

            # Final drain after stop signal
            for event in self._drain(page):
                self._capture_event(raw_steps, event, source="final")
            self._capture_checkpoint(page, page_checkpoints, reason="final")

        except Exception as exc:
            logger.error(f"[Recorder] Session error: {exc}")
        finally:
            try:
                browser.close()
                pw.stop()
            except Exception:
                pass

        steps = self._normalize(raw_steps, page_checkpoints)
        duration = time.time() - start_time

        result = RecordingResult(
            steps=steps,
            page_checkpoints=page_checkpoints,
            url=self._url,
            workflow_name=self._workflow_name,
            step_count=len(steps),
            duration=duration,
            captured_at=int(datetime.now().timestamp()),
        )
        logger.info(f"[Recorder] Complete: {len(steps)} steps in {duration:.1f}s")
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _drain(self, page: Page) -> List[Dict[str, Any]]:
        """Drain the fallback __rec_events array from the current page context."""
        try:
            return page.evaluate(RecorderScript.DRAIN_JS) or []
        except Exception:
            return []

    def _capture_event(self, raw_steps: List[Dict[str, Any]], event: Dict[str, Any], source: str) -> None:
        """Append an accepted recorded event and emit live operator feedback."""
        step = RecordedStep.from_raw(event)
        if not self._accept(step):
            return

        raw_steps.append(event)
        if source == "fallback":
            logger.info(f"[Recorder] Fallback captured: {step.type} '{step.display_label}'")
        else:
            logger.info(f"[Recorder] Captured: {step.type} '{step.display_label}'")

        self._emit_update(self._build_capture_feedback(step), "SUCCESS")

    def _capture_checkpoint(
        self,
        page: Page,
        page_checkpoints: List[Dict[str, Any]],
        reason: str,
    ) -> None:
        """Capture the current page field snapshot if it differs from the last one."""
        checkpoint = self._snapshot_page(page)
        if not checkpoint:
            return

        checkpoint["captured_at"] = int(datetime.now().timestamp())
        signature = str(checkpoint.get("signature") or "")
        if page_checkpoints and signature and signature == page_checkpoints[-1].get("signature"):
            return

        page_checkpoints.append(checkpoint)
        logger.info(
            "[Recorder] Page checkpoint (%s): %s (%s field(s))",
            reason,
            checkpoint.get("page_id") or checkpoint.get("title") or checkpoint.get("url") or "page",
            checkpoint.get("field_count", 0),
        )

    def _snapshot_page(self, page: Page) -> Dict[str, Any]:
        """Return a lightweight page checkpoint payload for future replay validation."""
        try:
            checkpoint = page.evaluate(RecorderScript.CHECKPOINT_JS) or {}
        except Exception:
            return {}

        if not isinstance(checkpoint, dict):
            return {}
        return checkpoint

    def _emit_update(self, message: str, log_type: str = "SYSTEM") -> None:
        if not self._update_callback:
            return
        try:
            self._update_callback(message, log_type)
        except TypeError:
            try:
                self._update_callback(message)
            except Exception:
                pass
        except Exception:
            pass

    @staticmethod
    def _build_capture_feedback(step: RecordedStep) -> str:
        target = step.display_label.strip() or step.selector.strip() or step.tag or "field"
        action = step.type.replace("_", " ").strip() or "step"
        masked_value = RecordingSession._preview_value(step)

        if masked_value:
            return f"Recorded {action}: {target} -> {masked_value}"
        return f"Recorded {action}: {target}"

    @staticmethod
    def _preview_value(step: RecordedStep) -> str:
        raw_value = (step.value or step.text or "").strip()
        if not raw_value:
            return ""

        sensitive_blob = " ".join([
            step.label,
            step.name,
            step.id,
            step.placeholder,
            step.input_type,
        ]).lower()
        if step.input_type.lower() == "password" or any(token in sensitive_blob for token in ("password", "passwd", "pwd", "ssn", "social security", "tax id", "tin")):
            return "[masked]"

        compact = " ".join(raw_value.split())
        if len(compact) > 48:
            return compact[:45] + "..."
        return compact

    def _wait_ready(self, page: Page, timeout: int = 10_000) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass

    @staticmethod
    def _accept(step: RecordedStep) -> bool:
        """Filter noise — only keep meaningful, user-triggered interactions."""
        if not step.type:
            return False
        # Skip non-interactive <input> element types (hidden placeholders, image maps).
        # NOTE: submit/button/reset ARE kept — the user intentionally clicked them
        # (Login submit is handled separately by playback auto-login logic).
        if step.tag == "input" and step.input_type in ("hidden", "image"):
            return False
        # Skip noise wrapper IDs
        if step.id.lower() in _NOISE_IDS:
            return False
        # Skip anonymous clicks only when they have no useful identity beyond a bare tag.
        if step.type in ("click", "click_link"):
            has_identity = any((step.text, step.id, step.name, step.label))
            selector = (step.selector or "").strip().lower()
            bare_tag = (step.tag or "").strip().lower()
            if not has_identity and (not selector or selector == bare_tag):
                return False
        return True

    def _normalize(
        self,
        raw_steps: List[Dict[str, Any]],
        page_checkpoints: Optional[List[Dict[str, Any]]] = None,
    ) -> List[RecordedStep]:
        """
        Clean captured events while preserving operator intent:
          - Keep intermediate edits so workflows reflect what the user actually did.
          - Remove only consecutive identical duplicate events.
          - Drop immediate same-page field events that fire after a successful
            navigation click but before the next page interaction arrives.
        """
        steps: List[RecordedStep] = []
        prev: Optional[RecordedStep] = None

        # Build final list, skipping only exact back-to-back duplicates.
        for raw in raw_steps:
            step = RecordedStep.from_raw(raw)
            # Drop consecutive identical steps (e.g. double-click)
            if self._is_duplicate_step(prev, step):
                continue
            steps.append(step)
            prev = step

        return self._drop_stale_post_navigation_steps(steps, page_checkpoints or [])

    @staticmethod
    def _step_page_identity(step: Optional[RecordedStep]) -> tuple[str, str, str]:
        if not step:
            return ("", "", "")
        return (step.page_id or "", step.page_url or "", step.page_title or "")

    @classmethod
    def _is_duplicate_step(cls, previous: Optional[RecordedStep], current: RecordedStep) -> bool:
        if previous is None:
            return False
        return (
            cls._step_page_identity(previous) == cls._step_page_identity(current)
            and previous.type == current.type
            and previous.id == current.id
            and previous.name == current.name
            and previous.selector == current.selector
            and previous.value == current.value
            and previous.text == current.text
        )

    @classmethod
    def _page_changed(
        cls,
        before: tuple[str, str, str],
        after: tuple[str, str, str],
    ) -> bool:
        if not any(before) or not any(after):
            return False
        if before[1] and after[1] and before[1] != after[1]:
            return True
        if before[0] and after[0] and before[0] != after[0]:
            return True
        return bool(before[2] and after[2] and before[2] != after[2])

    @staticmethod
    def _checkpoint_page_identity(checkpoint: Optional[Dict[str, Any]]) -> tuple[str, str, str]:
        if not checkpoint:
            return ("", "", "")
        return (
            str(checkpoint.get("page_id", "") or ""),
            str(checkpoint.get("url", "") or ""),
            str(checkpoint.get("title", "") or ""),
        )

    @staticmethod
    def _is_navigation_click(step: RecordedStep) -> bool:
        if step.step_type not in (StepType.CLICK, StepType.CLICK_LINK):
            return False

        blob = " ".join([
            step.id,
            step.name,
            step.label,
            step.text,
            step.selector,
            step.input_type,
        ]).lower()
        nav_tokens = (
            "submit",
            "next",
            "continue",
            "login",
            "sign in",
            "btnadd",
            "personadd",
            "activate",
            "employee administration",
            "add employee",
            "person_add",
        )
        return (step.input_type or "").lower() == "submit" or any(token in blob for token in nav_tokens)

    @classmethod
    def _is_same_page_fill_step(
        cls,
        step: RecordedStep,
        page_identity: tuple[str, str, str],
    ) -> bool:
        if step.step_type not in (StepType.INPUT, StepType.SELECT, StepType.TOGGLE):
            return False
        return cls._step_page_identity(step) == page_identity

    def _drop_stale_post_navigation_steps(
        self,
        steps: List[RecordedStep],
        page_checkpoints: List[Dict[str, Any]],
    ) -> List[RecordedStep]:
        if len(steps) < 2:
            return steps

        final_page_identity = self._checkpoint_page_identity(page_checkpoints[-1] if page_checkpoints else None)
        cleaned: List[RecordedStep] = []
        index = 0

        while index < len(steps):
            step = steps[index]
            cleaned.append(step)

            if not self._is_navigation_click(step):
                index += 1
                continue

            current_page = self._step_page_identity(step)
            stale_buffer: List[RecordedStep] = []
            lookahead = index + 1

            while lookahead < len(steps):
                candidate = steps[lookahead]
                if not self._is_same_page_fill_step(candidate, current_page):
                    break
                stale_buffer.append(candidate)
                lookahead += 1

            if not stale_buffer:
                index += 1
                continue

            next_page_identity = self._step_page_identity(steps[lookahead]) if lookahead < len(steps) else ("", "", "")
            advanced = self._page_changed(current_page, next_page_identity)
            if not advanced and lookahead >= len(steps):
                advanced = self._page_changed(current_page, final_page_identity)

            if advanced:
                preserved = [s for s in stale_buffer if self._should_preserve_post_navigation_step(s)]
                dropped = len(stale_buffer) - len(preserved)
                logger.info(
                    "[Recorder] Dropped %s stale post-navigation step(s) after '%s'",
                    dropped,
                    step.display_label,
                )
                cleaned.extend(preserved)
            else:
                cleaned.extend(stale_buffer)

            index = lookahead

        return cleaned

    @staticmethod
    def _should_preserve_post_navigation_step(step: RecordedStep) -> bool:
        """Keep real typed input events that can be emitted on blur after navigation clicks."""
        if step.step_type != StepType.INPUT:
            return False
        if not (step.value or "").strip():
            return False
        return any((step.id, step.name, step.label, step.selector))
