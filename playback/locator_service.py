"""
playback.locator_service
------------------------
Selector and locator helpers for playback sessions.
"""
from __future__ import annotations

import re
import time
from dataclasses import replace as dc_replace
from typing import Callable, List, Optional, Tuple

from playwright.sync_api import Locator, Page

from .models import StepType, WorkflowStep


class ElementLocator:
    """
    Finds a page element using multiple CSS / XPath strategies.
    Pure Playwright — no LLM, no page scanning.
    """

    def find(
        self,
        page: Page,
        step: WorkflowStep,
        timeout_ms: int = 8_000,
        allow_hidden_custom_widget: bool = False,
    ) -> Optional[Locator]:
        strategies = self._strategies(step)
        per_ms = max(500, timeout_ms // max(len(strategies), 1))

        for sel in strategies:
            try:
                loc = page.locator(sel)
                candidate = self._choose_candidate(
                    loc,
                    step,
                    allow_hidden_custom_widget=allow_hidden_custom_widget,
                )
                if candidate is not None:
                    try:
                        wait_state = self._wait_state(step, allow_hidden_custom_widget=allow_hidden_custom_widget)
                        candidate.wait_for(state=wait_state, timeout=per_ms)
                    except Exception:
                        pass
                    return candidate
            except Exception:
                continue
        return None

    def _choose_candidate(
        self,
        loc: Locator,
        step: WorkflowStep,
        allow_hidden_custom_widget: bool = False,
    ) -> Optional[Locator]:
        try:
            count = loc.count()
        except Exception:
            return None

        if count <= 0:
            return None

        desired_text = self._normalize_visible_text(step.text)

        if count == 1:
            candidate = loc.first
            if desired_text and step.step_type in (StepType.CLICK, StepType.CLICK_LINK):
                candidate_text = self._candidate_text(candidate)
                if not self._text_matches_desired(candidate_text, desired_text):
                    return None
            if self._requires_visible_field(step) and not allow_hidden_custom_widget:
                if not self._is_visible(candidate):
                    return None
            return candidate

        if not desired_text or step.step_type not in (StepType.CLICK, StepType.CLICK_LINK):
            for idx in range(min(count, 25)):
                candidate = loc.nth(idx)
                if self._is_visible(candidate):
                    return candidate
            if self._requires_visible_field(step) and not allow_hidden_custom_widget:
                return None
            return loc.first

        best_candidate: Optional[Locator] = None
        best_score = 0
        for idx in range(min(count, 25)):
            candidate = loc.nth(idx)
            if not self._is_visible(candidate):
                continue
            candidate_text = self._candidate_text(candidate)
            if not candidate_text:
                continue
            if candidate_text == desired_text:
                return candidate

            score = 0
            if desired_text in candidate_text:
                score += 2
            if candidate_text in desired_text:
                score += 1
            if score > best_score:
                best_candidate = candidate
                best_score = score

        return best_candidate if best_score > 0 else None

    @staticmethod
    def _is_visible(candidate: Locator) -> bool:
        try:
            return candidate.is_visible(timeout=500)
        except Exception:
            return False

    @staticmethod
    def _requires_visible_field(step: WorkflowStep) -> bool:
        return step.step_type in (StepType.INPUT, StepType.SELECT, StepType.DATE)

    @staticmethod
    def _wait_state(step: WorkflowStep, allow_hidden_custom_widget: bool = False) -> str:
        if step.step_type in (StepType.CLICK, StepType.CLICK_LINK):
            return "visible"
        if step.step_type in (StepType.INPUT, StepType.SELECT, StepType.DATE):
            return "attached" if allow_hidden_custom_widget else "visible"
        return "attached"

    @staticmethod
    def _candidate_text(candidate: Locator) -> str:
        try:
            text = candidate.inner_text(timeout=500)
        except Exception:
            text = ""

        if not text:
            for getter in (
                lambda: candidate.get_attribute("aria-label", timeout=500),
                lambda: candidate.get_attribute("title", timeout=500),
                lambda: candidate.get_attribute("value", timeout=500),
            ):
                try:
                    text = getter() or ""
                except Exception:
                    text = ""
                if text:
                    break

        return ElementLocator._normalize_visible_text(text)

    @staticmethod
    def _normalize_visible_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    @classmethod
    def _text_matches_desired(cls, candidate_text: str, desired_text: str) -> bool:
        candidate_text = cls._normalize_visible_text(candidate_text)
        desired_text = cls._normalize_visible_text(desired_text)
        if not candidate_text or not desired_text:
            return False
        return (
            candidate_text == desired_text
            or desired_text in candidate_text
            or candidate_text in desired_text
        )

    def _strategies(self, step: WorkflowStep) -> List[str]:
        out: List[str] = []

        if step.id:
            safe = step.id.replace("[", "\\[").replace("]", "\\]")
            out.append(f"#{safe}")
            out.append(f"[id='{step.id}']")

        if step.selector:
            out.append(step.selector)

        if step.name:
            out.append(f"[name='{step.name}']")

        if step.xpath:
            out.append(f"xpath={step.xpath}")

        tag = (step.tag or "").lower()
        if step.text and tag in ("button", "a", "input"):
            out.append(f"{tag}:has-text('{step.text[:60]}')")

        if step.aria_label:
            out.append(f"[aria-label='{step.aria_label}']")

        if step.label and step.step_type in (StepType.INPUT, StepType.SELECT, StepType.DATE):
            out.append(f"[placeholder='{step.label}']")

        return out


class LocatorService:
    """Retry/fallback locator orchestration preserving existing replay behavior."""

    def __init__(self, locator: Optional[ElementLocator] = None, retry_wait_s: float = 1.5) -> None:
        self._loc = locator or ElementLocator()
        self._retry_wait_s = retry_wait_s

    def find(
        self,
        page: Page,
        step: WorkflowStep,
        timeout_ms: int = 8_000,
        allow_hidden_custom_widget: bool = False,
    ) -> Optional[Locator]:
        return self._loc.find(
            page,
            step,
            timeout_ms=timeout_ms,
            allow_hidden_custom_widget=allow_hidden_custom_widget,
        )

    def find_with_retry(
        self,
        page: Page,
        step: WorkflowStep,
        retries: int = 2,
        log: Optional[Callable[..., None]] = None,
        on_healed: Optional[Callable[..., None]] = None,
        allow_hidden_custom_widget: bool = False,
    ) -> Optional[Locator]:
        if step.selector:
            for attempt in range(retries + 1):
                el = self._find_by_explicit_selector(
                    page,
                    step,
                    allow_hidden_custom_widget=allow_hidden_custom_widget,
                )
                if el:
                    return el

                try:
                    el = self._loc.find(
                        page,
                        dc_replace(step, selector=""),
                        allow_hidden_custom_widget=allow_hidden_custom_widget,
                    )
                except Exception:
                    el = None
                if el:
                    return el

                recovered, strategy, confidence, ambiguous = self._find_with_confident_identity_fallback(page, step)
                if recovered is not None and strategy:
                    if on_healed:
                        on_healed(page, step, strategy, confidence)
                    return recovered

                if attempt < retries:
                    suffix = "ambiguous fallback" if ambiguous else "selector miss"
                    if log:
                        log(f"  retry {attempt + 1}: '{step.display_label}' ({suffix})")
                    time.sleep(self._retry_wait_s)
            return None

        for attempt in range(retries + 1):
            el = self._loc.find(
                page,
                step,
                allow_hidden_custom_widget=allow_hidden_custom_widget,
            )
            if el:
                return el
            if attempt < retries:
                if log:
                    log(f"  retry {attempt + 1}: '{step.display_label}'")
                time.sleep(self._retry_wait_s)
        return None

    def _find_by_explicit_selector(
        self,
        page: Page,
        step: WorkflowStep,
        allow_hidden_custom_widget: bool = False,
    ) -> Optional[Locator]:
        if not step.selector:
            return None

        try:
            loc = page.locator(step.selector)
            candidate = self._loc._choose_candidate(
                loc,
                step,
                allow_hidden_custom_widget=allow_hidden_custom_widget,
            )
            if candidate is None:
                return None
            try:
                wait_state = self._loc._wait_state(step, allow_hidden_custom_widget=allow_hidden_custom_widget)
                candidate.wait_for(state=wait_state, timeout=1_200)
            except Exception:
                pass
            return candidate
        except Exception:
            return None

    def _find_with_confident_identity_fallback(
        self,
        page: Page,
        step: WorkflowStep,
    ) -> Tuple[Optional[Locator], str, float, bool]:
        """Resolve a selector miss using deterministic identity keys only."""
        ambiguous = False

        strategies: List[Tuple[str, str, float]] = []
        if step.id:
            strategies.append(("id", self._css_attr_equals("id", step.id), 0.99))
        if step.name:
            strategies.append(("name", self._css_attr_equals("name", step.name), 0.96))

        for strategy, selector, confidence in strategies:
            try:
                candidate, is_ambiguous = self._single_visible_candidate(page.locator(selector))
            except Exception:
                candidate, is_ambiguous = None, False
            ambiguous = ambiguous or is_ambiguous
            if candidate is not None:
                return candidate, strategy, confidence, ambiguous

        if step.label:
            try:
                candidate, is_ambiguous = self._single_visible_candidate(page.get_by_label(step.label, exact=True))
            except Exception:
                candidate, is_ambiguous = None, False
            ambiguous = ambiguous or is_ambiguous
            if candidate is not None:
                return candidate, "label", 0.90, ambiguous

        if step.placeholder:
            try:
                candidate, is_ambiguous = self._single_visible_candidate(page.get_by_placeholder(step.placeholder, exact=True))
            except Exception:
                candidate, is_ambiguous = None, False
            ambiguous = ambiguous or is_ambiguous
            if candidate is not None:
                return candidate, "placeholder", 0.86, ambiguous

        return None, "", 0.0, ambiguous

    def _single_visible_candidate(
        self,
        locator: Locator,
        probe_limit: int = 20,
    ) -> Tuple[Optional[Locator], bool]:
        """Return one visible candidate; flag ambiguity when multiple are visible."""
        try:
            count = locator.count()
        except Exception:
            return None, False

        if count <= 0:
            return None, False

        visible_matches: List[Locator] = []
        for idx in range(min(count, probe_limit)):
            candidate = locator.nth(idx)
            if self._locator_is_visible(candidate):
                visible_matches.append(candidate)
                if len(visible_matches) > 1:
                    return None, True

        if len(visible_matches) == 1:
            return visible_matches[0], False

        if count > 1:
            return None, True
        return None, False

    @staticmethod
    def _locator_is_visible(candidate: Locator) -> bool:
        try:
            return candidate.is_visible(timeout=500)
        except Exception:
            return False

    @staticmethod
    def _css_attr_equals(attr: str, value: str) -> str:
        escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
        return f"[{attr}=\"{escaped}\"]"
