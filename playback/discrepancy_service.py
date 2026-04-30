"""
playback.discrepancy_service
----------------------------
Discrepancy detection, dedupe, and verification helpers for playback sessions.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from playwright.sync_api import Page

from .models import DiscrepancyRecord, StepResult, StepType, WorkflowStep


class DiscrepancyService:
    """Extracted discrepancy logic that operates on PlaybackSession state."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def _remember_expected_page_field(self, step: WorkflowStep, sr: StepResult) -> None:
        if not self._should_track_expected_page_field(step, sr):
            return

        field = self._step_field_payload(step)
        keys = self._field_keys(field)
        if not keys:
            return

        self._current_page_recorded_fields[keys[0]] = field

    def _should_track_expected_page_field(self, step: WorkflowStep, sr: StepResult) -> bool:
        if not self._is_recordable_fill_step(step):
            return False

        if sr.skipped and self._is_intentional_fill_skip(step, sr):
            return False

        return True

    def _remember_fill_expectation(self, step: WorkflowStep, sr: StepResult) -> None:
        if not self._should_verify_filled_step(step, sr):
            return

        key = self._pending_fill_verification_key(step)
        if not key:
            return

        self._pending_fill_verifications[key] = {
            "step": step,
            "expected_value": self._expected_filled_value(step, sr),
        }

    def _should_verify_filled_step(self, step: WorkflowStep, sr: StepResult) -> bool:
        if not self._is_recordable_fill_step(step) or not sr.success:
            return False
        if not sr.skipped:
            return True

        msg = self._norm(sr.message)
        return "already populated with" in msg or "already in desired state" in msg

    def _expected_filled_value(self, step: WorkflowStep, sr: StepResult) -> str:
        if step.step_type in (StepType.INPUT, StepType.DATE):
            if step.is_password_field:
                return self._cfg.credentials.get("password") or step.value or ""
            if step.is_username_field:
                return self._cfg.credentials.get("username") or step.value or ""
            return sr.faker_value or step.value or ""

        if step.step_type == StepType.SELECT:
            value = sr.faker_value or step.value or step.text or ""
            if value.startswith("recorded:"):
                return value.split(":", 1)[1]
            return value

        return step.value or step.text or ""

    def _pending_fill_verification_key(self, step: WorkflowStep) -> str:
        field = self._step_field_payload(step)
        keys = self._field_keys(field)
        if keys:
            return keys[0]
        selector = self._norm(step.selector)
        if selector:
            return f"selector:{selector}"
        return f"step:{max(0, int(step.index))}"

    def _verify_pending_fill_states(self, page: Page, trigger: str = "") -> None:
        snapshot = self._sync_discrepancy_cache_with_page(page)
        if not snapshot or not self._pending_fill_verifications:
            return

        live_fields = snapshot.get("fields") if isinstance(snapshot, dict) else []
        if not isinstance(live_fields, list) or not live_fields:
            return

        live_key_set = self._field_key_set(live_fields)

        checked = 0
        failures = 0
        for item in self._pending_fill_verifications.values():
            step = item.get("step")
            if not isinstance(step, WorkflowStep):
                continue

            field_payload = self._step_field_payload(step)
            if not self._field_matches_key_set(field_payload, live_key_set):
                continue

            checked += 1
            self._result.fields_scanned += 1

            ok, message = self._filled_state_matches_current_page(
                page,
                step,
                str(item.get("expected_value") or ""),
            )
            if ok:
                continue

            failures += 1
            live_snapshot = snapshot if isinstance(snapshot, dict) and snapshot else {
                "page_id": step.page_id,
                "url": step.page_url,
            }
            self._append_discrepancy(
                kind="recorded_field_not_filled",
                live_snapshot=live_snapshot,
                field=field_payload,
                message=message,
                dedupe_key_extra=f"verify:{self._pending_fill_verification_key(step)}",
            )

        if checked and failures:
            self._log(
                f"Fill verification ({trigger or 'page'}): checked={checked}, not_filled={failures}",
                "WARNING",
            )

    def _filled_state_matches_current_page(
        self,
        page: Page,
        step: WorkflowStep,
        expected_value: str,
    ) -> Tuple[bool, str]:
        locator = self._loc.find(page, step, timeout_ms=1_200)
        if not locator:
            return False, "Filled field could not be located for verification"

        if step.step_type in (StepType.INPUT, StepType.DATE):
            fill_target = self._resolve_fill_locator(page, step, locator)
            actual_value = self._read_input_value(fill_target)
            if not self._input_value_matches(step, actual_value, expected_value) and fill_target != locator:
                actual_value = self._read_input_value(locator)
            if self._input_value_matches(step, actual_value, expected_value):
                return True, ""
            return False, f"Expected '{expected_value}' but field now contains '{actual_value}'"

        if step.step_type == StepType.SELECT:
            current_value, current_text = self._read_select_state(locator)
            if self._select_state_matches(step, current_value, current_text, expected_value):
                return True, ""
            actual = current_text or current_value
            return False, f"Expected '{expected_value or step.value or step.text}' but field now contains '{actual}'"

        if step.step_type == StepType.TOGGLE:
            input_type = (step.input_type or "").lower()
            if input_type == "radio":
                target = self._find_radio_option(page, step)
                if not target:
                    return False, "Expected toggle option could not be located for verification"
                try:
                    if target.is_checked(timeout=1_000):
                        return True, ""
                except Exception:
                    pass
                return False, "Expected toggle option is not selected"

            want = (step.value or "").lower() in ("true", "yes", "1", "on")
            try:
                checked = locator.is_checked(timeout=1_000)
            except Exception:
                checked = False
            if checked == want:
                return True, ""
            return False, f"Expected toggle to be {'on' if want else 'off'} but it is {'on' if checked else 'off'}"

        return True, ""

    def _clear_pending_fill_states_if_page_changed(
        self,
        before_snapshot: Optional[Dict[str, Any]],
        after_snapshot: Optional[Dict[str, Any]],
    ) -> None:
        before = before_snapshot or {}
        after = after_snapshot or {}

        if self._should_reset_page_cache(before, after):
            self._pending_fill_verifications.clear()
            self._current_page_recorded_fields.clear()

        if isinstance(after, dict) and after:
            self._last_discrepancy_snapshot = dict(after)

    def _sync_discrepancy_cache_with_page(self, page: Page) -> Dict[str, Any]:
        snapshot = self._scan_current_page_fields(page)
        if not snapshot:
            return {}

        before = self._last_discrepancy_snapshot if isinstance(self._last_discrepancy_snapshot, dict) else {}
        if self._should_reset_page_cache(before, snapshot):
            self._pending_fill_verifications.clear()
            self._current_page_recorded_fields.clear()

        self._last_discrepancy_snapshot = dict(snapshot)
        return snapshot

    def _should_reset_page_cache(self, before: Dict[str, Any], after: Dict[str, Any]) -> bool:
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

    def _record_healed_selector(
        self,
        page: Page,
        step: WorkflowStep,
        strategy: str,
        confidence: float,
    ) -> None:
        """Persist a healed selector event as an additive discrepancy record."""
        try:
            current_url = page.url
        except Exception:
            current_url = step.page_url or ""

        page_id = step.page_id or self._norm(urlparse(current_url).path) or "page"
        message = (
            f"Recovered after selector miss using {strategy} "
            f"(confidence={confidence:.2f})"
        )

        self._append_discrepancy(
            kind="healed_selector_match",
            live_snapshot={"page_id": page_id, "url": current_url},
            field=self._step_field_payload(step),
            message=message,
            dedupe_key_extra=f"step_{step.index + 1}",
        )

    def _track_recorded_field_not_filled(self, step: WorkflowStep, sr: StepResult) -> None:
        """Flag recorded form fields that were not actually filled during replay."""
        if not self._is_recordable_fill_step(step):
            return

        if sr.success and not sr.skipped:
            return

        if sr.skipped and self._is_intentional_fill_skip(step, sr):
            return

        message = sr.message or (
            "Recorded field was skipped" if sr.skipped else "Recorded field fill failed"
        )

        self._append_discrepancy(
            kind="recorded_field_not_filled",
            live_snapshot={"page_id": step.page_id, "url": step.page_url},
            field=self._step_field_payload(step),
            message=message,
            dedupe_key_extra=f"step_{step.index + 1}",
        )

    @staticmethod
    def _is_recordable_fill_step(step: WorkflowStep) -> bool:
        return step.step_type in (StepType.INPUT, StepType.SELECT, StepType.TOGGLE, StepType.DATE)

    def _is_intentional_fill_skip(self, step: WorkflowStep, sr: StepResult) -> bool:
        msg = self._norm(sr.message)
        if not msg:
            return False

        if "pre-skipped" in msg or "deferred until password filled" in msg:
            return True
        if "already populated with" in msg or "already in desired state" in msg:
            return True
        if "datepicker skipped" in msg:
            return True
        if "obsolete page context" in msg or "stale post-transition" in msg:
            return True
        if "decline control absent" in msg:
            return True
        if step.is_credential_field and "pre-skipped" in msg:
            return True
        return False

    @staticmethod
    def _step_field_payload(step: WorkflowStep) -> Dict[str, Any]:
        return {
            "id": step.id,
            "name": step.name,
            "label": step.label,
            "placeholder": step.placeholder,
            "aria_label": step.aria_label,
            "tag": step.tag,
            "input_type": step.input_type,
            "required": False,
        }

    def _collect_page_discrepancies(self, page: Page, trigger: str = "") -> None:
        """Compare current live page fields against the closest recorded checkpoint."""
        snapshot = self._sync_discrepancy_cache_with_page(page)
        if not snapshot:
            return

        signature = str(snapshot.get("signature") or "")
        if signature and signature in self._compared_page_signatures:
            return

        missing_fields: List[Dict[str, Any]] = []
        new_fields: List[Dict[str, Any]] = []
        renamed_fields: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        live_fields: List[Dict[str, Any]] = []

        if self._page_checkpoints:
            expected = self._select_expected_checkpoint(snapshot)
            if not expected:
                return
            missing_fields, new_fields, renamed_fields, live_fields = self._compare_checkpoint_fields(expected, snapshot)
        else:
            expected_fields = self._expected_visible_actionable_fields(list(self._current_page_recorded_fields.values()))
            if not expected_fields:
                return

            live_fields = [
                field for field in (snapshot.get("fields") or [])
                if isinstance(field, dict) and self._has_identity(field) and self._field_is_visible_actionable(field)
            ]
            missing_fields, new_fields, renamed_fields = self._compare_field_sets(expected_fields, live_fields)

        self._result.fields_scanned += len(live_fields)

        screenshot_path: Optional[str] = None
        if missing_fields or new_fields or renamed_fields:
            screenshot_path = self._qa_discrepancy_screenshot(
                page,
                trigger=trigger,
                missing_count=len(missing_fields),
                new_count=len(new_fields),
                renamed_count=len(renamed_fields),
            )

        for field in missing_fields:
            self._append_discrepancy(
                kind="missing_recorded_field",
                live_snapshot=snapshot,
                field=field,
                message=self._field_summary_message("Recorded field not found on current page", field),
                screenshot_path=screenshot_path,
            )

        for field in new_fields:
            self._append_discrepancy(
                kind="new_unexpected_field",
                live_snapshot=snapshot,
                field=field,
                message=self._field_summary_message("New live field not present in recording", field),
                screenshot_path=screenshot_path,
            )

        for expected_field, live_field in renamed_fields:
            self._append_discrepancy(
                kind="renamed_recorded_field",
                live_snapshot=snapshot,
                field=expected_field,
                message=self._renamed_field_message(expected_field, live_field),
                dedupe_key_extra=self._field_identity_string(live_field),
                screenshot_path=screenshot_path,
                expected_field=expected_field,
                live_field=live_field,
            )

        if signature:
            self._compared_page_signatures.add(signature)
        self._result.pages_compared += 1

        if missing_fields or new_fields or renamed_fields:
            self._log(
                f"Checkpoint discrepancy ({trigger or 'page'}): page={snapshot.get('page_id') or 'unknown'}, "
                f"missing={len(missing_fields)}, new={len(new_fields)}, renamed={len(renamed_fields)}",
                "WARNING",
            )

    def _scan_current_page_fields(self, page: Page) -> Dict[str, Any]:
        """Collect a lightweight snapshot of visible form fields on the current page."""
        try:
            snapshot = page.evaluate(
                r"""() => {
                    function txt(v) {
                        return String(v || '').replace(/\s+/g, ' ').trim();
                    }

                    function slug(v) {
                        return txt(v)
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, '_')
                            .replace(/^_+|_+$/g, '')
                            .substring(0, 80);
                    }

                    function getLabel(el) {
                        if (el.labels && el.labels[0]) return txt(el.labels[0].innerText || el.labels[0].textContent);
                        var aria = el.getAttribute('aria-label');
                        if (aria) return txt(aria);
                        var labelledBy = el.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            var lbl = document.getElementById(labelledBy.split(' ')[0]);
                            if (lbl) return txt(lbl.innerText || lbl.textContent);
                        }
                        if (el.placeholder) return txt(el.placeholder);
                        var prev = el.previousElementSibling;
                        if (prev) return txt(prev.innerText || prev.textContent);
                        return '';
                    }

                    function visibleTextAround(el) {
                        var parts = [];
                        var parent = el.closest('.form-group, .row, .field, li, tr, div') || el.parentElement;
                        if (parent) parts.push(txt(parent.innerText || parent.textContent).substring(0, 180));
                        return parts.filter(Boolean).slice(0, 3);
                    }

                    function isChosenHidden(el) {
                        if (!el || el.tagName !== 'SELECT' || !el.id) return false;
                        var chosen = document.getElementById(el.id + '_chosen') || document.getElementById(el.id + '_chzn');
                        if (chosen) return isVisible(chosen);
                        var sib = el.nextElementSibling;
                        if (sib && (sib.classList.contains('chosen-container') || sib.classList.contains('chzn-container'))) return isVisible(sib);
                        return false;
                    }

                    function isVisible(el) {
                        if (!el || el.closest('template,[aria-hidden="true"],[hidden],.field-template,.template,.prototype')) return false;
                        var style = window.getComputedStyle(el);
                        return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                            && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && Number(style.opacity || '1') !== 0;
                    }

                    function isTemplateField(el) {
                        var blob = txt([el.id || '', el.name || '', el.getAttribute('data-valmsg-for') || ''].join(' ')).toLowerCase();
                        return /(__prefix__|__name__|data-template|prototype|template-row|template_field)/.test(blob);
                    }

                    function keepField(el, tag, inputType, chosen) {
                        if (isTemplateField(el)) return false;
                        if (tag === 'input' && ['hidden', 'submit', 'button', 'file', 'image', 'reset'].indexOf(inputType) >= 0) {
                            return false;
                        }
                        if (el.disabled) return false;
                        if (el.readOnly && tag !== 'select') return false;
                        if (!chosen && !isVisible(el)) return false;
                        return true;
                    }

                    var url = String(window.location.href || '');
                    var path = String(window.location.pathname || '');
                    var title = txt(document.title || '');
                    var pageId = slug([path, title].join(' ')) || 'page';

                    var fields = Array.from(document.querySelectorAll('input, textarea, select'))
                        .map(function(el) {
                            var tag = (el.tagName || '').toLowerCase();
                            var inputType = txt(el.type || (tag === 'select' ? 'select-one' : ''));
                            var chosen = isChosenHidden(el);
                            var visible = isVisible(el) || chosen;

                            if (!visible) return null;
                            if (!keepField(el, tag, inputType, chosen)) return null;

                            return {
                                tag: tag,
                                input_type: inputType,
                                id: el.id || '',
                                name: el.name || '',
                                label: getLabel(el),
                                placeholder: el.placeholder || '',
                                aria_label: el.getAttribute('aria-label') || '',
                                visible: true,
                                actionable: true,
                                required: !!(el.required || el.getAttribute('aria-required') === 'true'),
                                readonly: !!el.readOnly,
                                disabled: !!el.disabled,
                                chosen: !!chosen,
                                nearby_text: visibleTextAround(el),
                            };
                        })
                        .filter(Boolean);

                    var signature = [
                        pageId,
                        path,
                        title,
                        fields.map(function(field) {
                            return [field.id, field.name, field.label, field.tag, field.input_type].join(':');
                        }).join('|'),
                    ].join('|');

                    return {
                        page_id: pageId,
                        url: url,
                        path: path,
                        title: title,
                        fields: fields,
                        field_count: fields.length,
                        signature: signature,
                    };
                }"""
            ) or {}
        except Exception:
            return {}

        return snapshot if isinstance(snapshot, dict) else {}

    def _select_expected_checkpoint(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Choose the best recorded checkpoint for the current page snapshot."""
        if not self._page_checkpoints:
            return None

        live_page_id = self._norm(snapshot.get("page_id"))
        live_path = self._norm(snapshot.get("path"))
        live_url = self._norm(snapshot.get("url"))
        live_title = self._norm(snapshot.get("title"))

        best: Optional[Dict[str, Any]] = None
        best_score = 0

        for checkpoint in self._page_checkpoints:
            score = 0
            cp_page_id = self._norm(checkpoint.get("page_id"))
            cp_path = self._norm(checkpoint.get("path"))
            cp_url = self._norm(checkpoint.get("url"))
            cp_title = self._norm(checkpoint.get("title"))

            if cp_page_id and live_page_id and cp_page_id == live_page_id:
                score += 6
            if cp_path and live_path and cp_path == live_path:
                score += 5
            if cp_url and live_url and cp_url == live_url:
                score += 4
            elif cp_url and live_path:
                cp_url_path = self._norm(urlparse(cp_url).path)
                if cp_url_path and cp_url_path == live_path:
                    score += 3
            if cp_title and live_title and cp_title == live_title:
                score += 2

            if score > best_score:
                best = checkpoint
                best_score = score

        return best if best_score > 0 else None

    def _compare_checkpoint_fields(
        self,
        checkpoint: Dict[str, Any],
        snapshot: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Tuple[Dict[str, Any], Dict[str, Any]]], List[Dict[str, Any]]]:
        """Return missing, new, renamed, and live visible fields for a page."""
        expected_fields = self._expected_visible_actionable_fields(
            [f for f in (checkpoint.get("fields") or []) if isinstance(f, dict)]
        )
        live_fields = [
            f for f in (snapshot.get("fields") or [])
            if isinstance(f, dict) and self._has_identity(f) and self._field_is_visible_actionable(f)
        ]

        missing, new_fields, renamed = self._compare_field_sets(expected_fields, live_fields)
        return missing, new_fields, renamed, live_fields

    def _compare_field_sets(
        self,
        expected_fields: List[Dict[str, Any]],
        live_fields: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Tuple[Dict[str, Any], Dict[str, Any]]]]:
        expected_keys = self._field_key_set(expected_fields)
        live_keys = self._field_key_set(live_fields)

        missing = [
            field for field in expected_fields
            if not self._field_matches_key_set(field, live_keys)
        ]
        new_fields = [
            field for field in live_fields
            if not self._field_matches_key_set(field, expected_keys)
        ]

        renamed = self._pair_likely_renamed_fields(missing, new_fields)
        if renamed:
            renamed_expected_ids = {id(expected) for expected, _ in renamed}
            renamed_live_ids = {id(live) for _, live in renamed}
            missing = [field for field in missing if id(field) not in renamed_expected_ids]
            new_fields = [field for field in new_fields if id(field) not in renamed_live_ids]

        return missing, new_fields, renamed

    def _expected_visible_actionable_fields(self, fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            field for field in fields
            if isinstance(field, dict)
            and self._has_identity(field)
            and self._field_is_visible_actionable(field)
        ]

    def _field_is_visible_actionable(self, field: Dict[str, Any]) -> bool:
        tag = self._norm(field.get("tag"))
        input_type = self._norm(field.get("input_type"))

        if tag == "input" and input_type in {"hidden", "submit", "button", "file", "image", "reset"}:
            return False
        if bool(field.get("disabled", False)):
            return False
        if bool(field.get("readonly", False)) and tag != "select":
            return False
        if field.get("visible") is False and not bool(field.get("chosen", False)):
            return False

        identity_blob = " ".join(
            str(field.get(key) or "") for key in ("id", "name", "label", "placeholder", "aria_label")
        ).lower()
        if any(token in identity_blob for token in ("__prefix__", "__name__", "template-row", "template_field")):
            return False
        return True

    def _pair_likely_renamed_fields(
        self,
        missing_fields: List[Dict[str, Any]],
        new_fields: List[Dict[str, Any]],
    ) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        used_live: Set[int] = set()

        for expected in missing_fields:
            best_live: Optional[Dict[str, Any]] = None
            best_score = 0
            for live in new_fields:
                if id(live) in used_live:
                    continue
                score = self._rename_similarity_score(expected, live)
                if score > best_score:
                    best_score = score
                    best_live = live

            if best_live is not None and best_score >= 8:
                pairs.append((expected, best_live))
                used_live.add(id(best_live))

        return pairs

    def _rename_similarity_score(self, expected: Dict[str, Any], live: Dict[str, Any]) -> int:
        score = 0
        if self._norm(expected.get("tag")) and self._norm(expected.get("tag")) == self._norm(live.get("tag")):
            score += 2
        if self._norm(expected.get("input_type")) and self._norm(expected.get("input_type")) == self._norm(live.get("input_type")):
            score += 2
        if self._norm(expected.get("label")) and self._norm(expected.get("label")) == self._norm(live.get("label")):
            score += 6
        if self._norm(expected.get("placeholder")) and self._norm(expected.get("placeholder")) == self._norm(live.get("placeholder")):
            score += 4
        if self._norm(expected.get("aria_label")) and self._norm(expected.get("aria_label")) == self._norm(live.get("aria_label")):
            score += 4
        if bool(expected.get("required", False)) == bool(live.get("required", False)):
            score += 1

        expected_nearby = self._nearby_text_tokens(expected)
        live_nearby = self._nearby_text_tokens(live)
        if expected_nearby and live_nearby and expected_nearby.intersection(live_nearby):
            score += 2

        return score

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
        """Append a discrepancy once per unique page/field identity."""
        page_id = str(live_snapshot.get("page_id") or "")
        page_url = str(live_snapshot.get("url") or "")

        expected_field = expected_field or (field if kind in {"missing_recorded_field", "renamed_recorded_field"} else {})
        live_field = live_field or (field if kind in {"new_unexpected_field", "renamed_recorded_field"} else {})

        identity = self._discrepancy_identity(field)
        dedupe_parts = [kind, page_id, identity]
        if dedupe_key_extra:
            dedupe_parts.append(dedupe_key_extra)
        dedupe_key = "|".join(dedupe_parts)
        if dedupe_key in self._seen_discrepancy_keys:
            return

        self._seen_discrepancy_keys.add(dedupe_key)
        self._result.discrepancies.append(
            DiscrepancyRecord(
                kind=kind,
                page_id=page_id,
                page_url=page_url,
                field_label=str(field.get("label") or ""),
                field_id=str(field.get("id") or ""),
                field_name=str(field.get("name") or ""),
                tag=str(field.get("tag") or ""),
                input_type=str(field.get("input_type") or ""),
                field_required=bool(field.get("required", False)),
                field_visible=bool(field.get("visible", True)),
                field_actionable=bool(field.get("actionable", self._field_is_visible_actionable(field))),
                expected_field_id=str(expected_field.get("id") or ""),
                expected_field_name=str(expected_field.get("name") or ""),
                expected_field_label=str(expected_field.get("label") or ""),
                live_field_id=str(live_field.get("id") or ""),
                live_field_name=str(live_field.get("name") or ""),
                live_field_label=str(live_field.get("label") or ""),
                message=message,
                screenshot_path=screenshot_path,
            )
        )

    def _field_summary_message(self, prefix: str, field: Dict[str, Any]) -> str:
        parts = [prefix]
        display = self._field_display_name(field)
        if display:
            parts.append(f"field='{display}'")
        if bool(field.get("required", False)):
            parts.append("required=true")
        type_bits = "/".join(bit for bit in (str(field.get("tag") or ""), str(field.get("input_type") or "")) if bit)
        if type_bits:
            parts.append(f"type={type_bits}")
        return "; ".join(parts)

    def _renamed_field_message(self, expected: Dict[str, Any], live: Dict[str, Any]) -> str:
        expected_identity = self._field_identity_string(expected)
        live_identity = self._field_identity_string(live)
        display = self._field_display_name(expected) or self._field_display_name(live) or "field"
        return (
            f"Recorded field appears renamed: '{display}'. "
            f"Expected {expected_identity}; live {live_identity}."
        )

    def _field_display_name(self, field: Dict[str, Any]) -> str:
        for key in ("label", "aria_label", "placeholder", "name", "id"):
            value = str(field.get(key) or "").strip()
            if value:
                return value
        return ""

    def _field_identity_string(self, field: Dict[str, Any]) -> str:
        bits = []
        for label, key in (("id", "id"), ("name", "name"), ("label", "label"), ("type", "input_type")):
            value = str(field.get(key) or "").strip()
            if value:
                bits.append(f"{label}='{value}'")
        return ", ".join(bits) if bits else "unnamed field"

    def _nearby_text_tokens(self, field: Dict[str, Any]) -> Set[str]:
        raw = field.get("nearby_text") or []
        if isinstance(raw, str):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = [str(item or "") for item in raw]
        else:
            raw_items = []
        text = " ".join(raw_items).lower()
        return {token for token in re.findall(r"[a-z0-9]{3,}", text) if token not in {"the", "and", "for", "with"}}

    def _qa_discrepancy_screenshot(
        self,
        page: Page,
        trigger: str,
        missing_count: int,
        new_count: int,
        renamed_count: int = 0,
    ) -> Optional[str]:
        """Capture one screenshot as evidence for a page-level QA discrepancy."""
        try:
            shot_dir = os.path.join(os.getcwd(), "Screenshots")
            os.makedirs(shot_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            safe_trigger = re.sub(r"[^\w\-]+", "_", trigger or "page")[:30]
            path = os.path.join(shot_dir, f"QA_{safe_trigger}_{ts}.png")

            summary = f"QA discrepancy: missing={missing_count}, new={new_count}, renamed={renamed_count}"
            summary_js = summary[:120].replace("\\", "\\\\").replace("`", "\\`").replace("'", "\\'")

            page.evaluate(
                f"""() => {{
                    try {{
                        const existing = document.getElementById('__pb_qa_banner__');
                        if (existing) existing.remove();
                        const d = document.createElement('div');
                        d.id = '__pb_qa_banner__';
                        d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;'
                            + 'background:#92400e;color:#fff;padding:12px 20px;'
                            + 'font:bold 13px Arial,sans-serif;text-align:center;'
                            + 'border-bottom:4px solid #7c2d12;box-shadow:0 2px 8px rgba(0,0,0,.5);';
                        d.textContent = '{summary_js}';
                        document.body && document.body.prepend(d);
                    }} catch(e) {{}}
                }}"""
            )

            page.screenshot(path=path, full_page=True)

            page.evaluate(
                "() => { const b = document.getElementById('__pb_qa_banner__'); if (b) b.remove(); }"
            )

            self._log(f"  qa discrepancy screenshot: {path}")
            return path
        except Exception:
            return None

    def _has_identity(self, field: Dict[str, Any]) -> bool:
        return any(
            self._norm(field.get(key))
            for key in ("id", "name", "label", "placeholder", "aria_label")
        )

    def _field_key_set(self, fields: List[Dict[str, Any]]) -> Set[str]:
        keys: Set[str] = set()
        for field in fields:
            for key in self._field_keys(field):
                keys.add(key)
        return keys

    def _field_matches_key_set(self, field: Dict[str, Any], key_set: Set[str]) -> bool:
        for key in self._field_keys(field):
            if key in key_set:
                return True
        return False

    def _field_keys(self, field: Dict[str, Any]) -> List[str]:
        keys: List[str] = []
        tag = self._norm(field.get("tag"))
        input_type = self._norm(field.get("input_type"))
        field_id = self._norm(field.get("id"))
        name = self._norm(field.get("name"))
        label = self._norm(field.get("label"))
        placeholder = self._norm(field.get("placeholder"))
        aria_label = self._norm(field.get("aria_label"))
        has_structural_identity = bool(field_id or name)

        if field_id:
            keys.append(f"id:{field_id}")
            dynamic = self._dynamic_identity(field_id)
            if dynamic and dynamic != field_id:
                keys.append(f"id_dynamic:{dynamic}|{tag}|{input_type}")
        if name:
            keys.append(f"name:{name}")
            dynamic = self._dynamic_identity(name)
            if dynamic and dynamic != name:
                keys.append(f"name_dynamic:{dynamic}|{tag}|{input_type}")
        if label and not has_structural_identity:
            keys.append(f"label:{label}|{tag}|{input_type}")
        if placeholder and not has_structural_identity:
            keys.append(f"placeholder:{placeholder}|{tag}|{input_type}")
        if aria_label and not has_structural_identity:
            keys.append(f"aria:{aria_label}|{tag}|{input_type}")

        return keys

    @staticmethod
    def _dynamic_identity(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return ""
        normalized = re.sub(r"\[\d+\]", "[*]", normalized)
        normalized = re.sub(r"_\d+__", "_*__", normalized)
        normalized = re.sub(r"productlst_\d+_", "productlst_*_", normalized)
        return normalized

    def _discrepancy_identity(self, field: Dict[str, Any]) -> str:
        candidates = [
            f"id:{self._norm(field.get('id'))}",
            f"name:{self._norm(field.get('name'))}",
            f"label:{self._norm(field.get('label'))}",
            f"placeholder:{self._norm(field.get('placeholder'))}",
        ]
        for candidate in candidates:
            if candidate.split(":", 1)[1]:
                return candidate
        return f"field:{self._norm(field.get('tag'))}:{self._norm(field.get('input_type'))}"
