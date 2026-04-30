"""
playback.models
---------------
Pure data models for workflow playback. No external dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class StepType(Enum):
    INPUT = "input"
    SELECT = "select"
    CLICK = "click"
    CLICK_LINK = "click_link"
    TOGGLE = "toggle"
    DATE = "date"


@dataclass
class WorkflowStep:
    """A single recorded workflow step."""

    type: str
    page_id: str = ""
    page_url: str = ""
    page_title: str = ""
    tag: str = ""
    id: str = ""
    name: str = ""
    label: str = ""
    text: str = ""
    value: str = ""
    selector: str = ""
    input_type: str = ""
    placeholder: str = ""
    aria_label: str = ""
    xpath: str = ""
    index: int = -1
    # Runtime flags — not serialized
    skip: bool = field(default=False, repr=False)
    executed: bool = field(default=False, repr=False)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], index: int = -1) -> "WorkflowStep":
        return cls(
            type=raw.get("type", ""),
            page_id=raw.get("page_id", ""),
            page_url=raw.get("page_url", ""),
            page_title=raw.get("page_title", ""),
            tag=raw.get("tag", ""),
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            label=raw.get("label", ""),
            text=raw.get("text", ""),
            value=raw.get("value", ""),
            selector=raw.get("selector", ""),
            input_type=raw.get("input_type", ""),
            placeholder=raw.get("placeholder", ""),
            aria_label=raw.get("aria_label", ""),
            xpath=raw.get("xpath", ""),
            index=index,
        )

    @property
    def step_type(self) -> Optional[StepType]:
        try:
            return StepType(self.type.lower())
        except (ValueError, AttributeError):
            return None

    @property
    def is_password_field(self) -> bool:
        return (
            (self.input_type or "").lower() == "password"
            or "password" in (self.id or self.name or "").lower()
        )

    @property
    def is_username_field(self) -> bool:
        norm = (self.id or self.name or self.label or "").lower()
        return any(k in norm for k in ("username", "userid", "loginid", "login_id"))

    @property
    def is_credential_field(self) -> bool:
        return self.is_password_field or self.is_username_field

    @property
    def is_login_submit(self) -> bool:
        if "click" not in (self.type or "").lower():
            return False
        combined = " ".join([self.text or "", self.label or "", self.name or ""]).lower()
        return any(k in combined for k in ("login", "sign in", "logon")) or (
            "login" in (self.selector or "").lower()
        )

    @property
    def display_label(self) -> str:
        return self.label or self.text or self.name or self.id or f"step_{self.index}"


@dataclass
class PlaybackConfig:
    """Configuration for a single playback session."""

    workflow_data: Dict[str, Any]
    execution_profile: Dict[str, Any]
    credentials: Dict[str, str]
    start_url: str
    headless: bool = False
    group_name: str = ""
    update_callback: Optional[Callable] = None
    speed_factor: float = 1.0
    # Path for the Excel run-report; auto-generated under reports/ when blank
    excel_report_path: str = ""
    replay_mode: str = "standard"
    fail_on_missing_required_fields: bool = True
    fail_on_new_required_fields: bool = False
    fail_on_not_filled_fields: bool = True


@dataclass
class StepResult:
    """Result of executing a single step."""

    step_label: str
    success: bool
    skipped: bool = False
    message: str = ""
    # Value that was actually used to fill the field (Faker-generated or recorded)
    faker_value: str = ""
    # Path to an annotated failure screenshot, if captured
    screenshot_path: Optional[str] = None


@dataclass
class DiscrepancyRecord:
    """A discrepancy found while comparing recorded vs live page fields."""

    kind: str
    page_id: str = ""
    page_url: str = ""
    field_label: str = ""
    field_id: str = ""
    field_name: str = ""
    tag: str = ""
    input_type: str = ""
    field_required: bool = False
    field_visible: bool = True
    field_actionable: bool = True
    expected_field_id: str = ""
    expected_field_name: str = ""
    expected_field_label: str = ""
    live_field_id: str = ""
    live_field_name: str = ""
    live_field_label: str = ""
    severity: str = ""
    message: str = ""
    screenshot_path: Optional[str] = None

    @staticmethod
    def infer_severity(kind: str) -> str:
        mapping = {
            "missing_recorded_field": "blocker",
            "recorded_field_not_filled": "blocker",
            "new_unexpected_field": "warning",
            "renamed_recorded_field": "warning",
            "healed_selector_match": "info",
        }
        return mapping.get((kind or "").strip().lower(), "warning")

    @property
    def resolved_severity(self) -> str:
        raw = (self.severity or "").strip().lower()
        if raw in {"blocker", "warning", "info"}:
            return raw
        return self.infer_severity(self.kind)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "field_label": self.field_label,
            "field_id": self.field_id,
            "field_name": self.field_name,
            "tag": self.tag,
            "input_type": self.input_type,
            "field_required": bool(self.field_required),
            "field_visible": bool(self.field_visible),
            "field_actionable": bool(self.field_actionable),
            "expected_field_id": self.expected_field_id,
            "expected_field_name": self.expected_field_name,
            "expected_field_label": self.expected_field_label,
            "live_field_id": self.live_field_id,
            "live_field_name": self.live_field_name,
            "live_field_label": self.live_field_label,
            "severity": self.resolved_severity,
            "message": self.message,
            "screenshot_path": self.screenshot_path,
            "screenshot": self.screenshot_path,
        }


@dataclass
class PlaybackResult:
    """Result of an entire playback session."""

    status: str = "failed"
    steps_executed: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0
    error: Optional[str] = None
    screenshot_path: Optional[str] = None
    step_results: List[StepResult] = field(default_factory=list)
    discrepancies: List[DiscrepancyRecord] = field(default_factory=list)
    pages_compared: int = 0
    fields_scanned: int = 0
    duration_seconds: float = 0.0
    replay_mode: str = "standard"
    fail_on_missing_required_fields: bool = True
    fail_on_new_required_fields: bool = False
    fail_on_not_filled_fields: bool = True
    data_generation: Dict[str, Any] = field(default_factory=dict)
    runtime_data: Dict[str, Any] = field(default_factory=dict)
    excel_report_path: str = ""
    master_data_path: str = ""
    master_data_run_id: str = ""

    @staticmethod
    def _normalize_mode(value: Any) -> str:
        mode = str(value or "standard").strip().lower()
        return mode if mode in {"lenient", "standard", "strict"} else "standard"

    def _apply_policy_severity(self, item: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(item)
        kind = str(out.get("kind") or "")
        severity = str(out.get("severity") or "warning").strip().lower()
        required = bool(out.get("field_required", False))

        if kind == "missing_recorded_field" and required:
            if self.fail_on_missing_required_fields:
                severity = "blocker"
            elif severity == "blocker":
                severity = "warning"

        if kind == "new_unexpected_field" and required and self.fail_on_new_required_fields:
            severity = "blocker"

        if kind == "recorded_field_not_filled" and not self.fail_on_not_filled_fields and severity == "blocker":
            severity = "warning"

        out["severity"] = severity if severity in {"blocker", "warning", "info"} else "warning"
        return out

    @staticmethod
    def _build_instability_indicators(discrepancies: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        page_counts: Dict[str, Dict[str, Any]] = {}
        missing_field_counts: Dict[str, Dict[str, Any]] = {}

        for item in discrepancies:
            page_key = str(item.get("page_id") or item.get("page_url") or "unknown_page").strip() or "unknown_page"
            page_entry = page_counts.setdefault(
                page_key,
                {"page_id": str(item.get("page_id") or ""), "page_url": str(item.get("page_url") or ""), "count": 0, "blockers": 0, "warnings": 0},
            )
            page_entry["count"] += 1

            severity = str(item.get("severity") or "warning").strip().lower()
            if severity == "blocker":
                page_entry["blockers"] += 1
            elif severity == "warning":
                page_entry["warnings"] += 1

            if str(item.get("kind") or "") != "missing_recorded_field":
                continue

            field_key = (
                str(item.get("field_id") or "").strip()
                or str(item.get("field_name") or "").strip()
                or str(item.get("field_label") or "").strip()
                or "unknown_field"
            )
            field_entry = missing_field_counts.setdefault(
                field_key,
                {
                    "field_key": field_key,
                    "field_label": str(item.get("field_label") or ""),
                    "field_id": str(item.get("field_id") or ""),
                    "field_name": str(item.get("field_name") or ""),
                    "count": 0,
                },
            )
            field_entry["count"] += 1

        pages_with_most_discrepancies = sorted(
            page_counts.values(),
            key=lambda row: (int(row.get("count", 0)), int(row.get("blockers", 0))),
            reverse=True,
        )[:5]

        fields_frequently_missing = sorted(
            missing_field_counts.values(),
            key=lambda row: int(row.get("count", 0)),
            reverse=True,
        )[:5]

        return {
            "pages_with_most_discrepancies": pages_with_most_discrepancies,
            "fields_frequently_missing": fields_frequently_missing,
        }

    def to_dict(self) -> Dict[str, Any]:
        def _sr(r: "StepResult") -> Dict[str, Any]:
            return {
                "step":           r.step_label,
                "success":        r.success,
                "skipped":        r.skipped,
                "message":        r.message,
                "faker_value":    r.faker_value,
                "screenshot":     r.screenshot_path,
            }

        def _dr(r: "DiscrepancyRecord") -> Dict[str, Any]:
            return r.to_dict()

        serialised = [_sr(r) for r in self.step_results]
        mode = self._normalize_mode(self.replay_mode)
        data_meta = self.data_generation if isinstance(self.data_generation, dict) else {}
        discrepancies = [self._apply_policy_severity(_dr(r)) for r in self.discrepancies]
        missing_count = sum(1 for item in discrepancies if item.get("kind") == "missing_recorded_field")
        new_count = sum(1 for item in discrepancies if item.get("kind") == "new_unexpected_field")
        renamed_count = sum(1 for item in discrepancies if item.get("kind") == "renamed_recorded_field")
        healed_count = sum(1 for item in discrepancies if item.get("kind") == "healed_selector_match")
        not_filled_count = sum(1 for item in discrepancies if item.get("kind") == "recorded_field_not_filled")
        blocker_count = sum(1 for item in discrepancies if item.get("severity") == "blocker")
        warning_count = sum(1 for item in discrepancies if item.get("severity") == "warning")
        info_count = sum(1 for item in discrepancies if item.get("severity") == "info")
        fields_scanned = max(0, int(self.fields_scanned or 0))

        qa_outcome = "Passed"
        base_failed = self.status != "finished" or self.steps_failed > 0
        discrepancy_failed = False
        if mode == "strict":
            discrepancy_failed = blocker_count > 0 or warning_count > 0
        elif mode == "standard":
            discrepancy_failed = blocker_count > 0
        elif mode == "lenient":
            discrepancy_failed = False

        if base_failed or discrepancy_failed:
            qa_outcome = "Failed"
        elif warning_count > 0:
            qa_outcome = "Passed with warnings"

        qa_summary = {
            "replay_mode": mode,
            "missing_fields": missing_count,
            "new_fields": new_count,
            "renamed_fields": renamed_count,
            "healed_matches": healed_count,
            "not_filled": not_filled_count,
            "warnings": warning_count,
            "blockers": blocker_count,
            "info": info_count,
            "total_discrepancies": len(discrepancies),
            "fail_on_missing_required_fields": bool(self.fail_on_missing_required_fields),
            "fail_on_new_required_fields": bool(self.fail_on_new_required_fields),
            "fail_on_not_filled_fields": bool(self.fail_on_not_filled_fields),
        }

        run_metrics = {
            "pages_compared": int(self.pages_compared or 0),
            "fields_scanned": fields_scanned,
            "healed_matches": healed_count,
            "warnings": warning_count,
            "blockers": blocker_count,
            "runtime_duration_seconds": float(self.duration_seconds or 0.0),
            "data_source": str(data_meta.get("source") or "faker"),
            "data_seed": data_meta.get("seed"),
            "data_corrections": len(data_meta.get("corrections") or []),
        }

        instability_indicators = self._build_instability_indicators(discrepancies)
        trend_ready = {
            "schema": "qa_run_metrics_v1",
            "replay_mode": mode,
            "qa_outcome": qa_outcome,
            "run_metrics": run_metrics,
            "severity_counts": {
                "blocker": blocker_count,
                "warning": warning_count,
                "info": info_count,
            },
            "discrepancy_counts": {
                "missing_fields": missing_count,
                "new_fields": new_count,
                "renamed_fields": renamed_count,
                "healed_matches": healed_count,
                "not_filled": not_filled_count,
            },
        }
        return {
            "status":          self.status,
            "steps_executed":  self.steps_executed,
            "steps_failed":    self.steps_failed,
            "steps_skipped":   self.steps_skipped,
            "error":           self.error,
            "screenshot_path": self.screenshot_path,
            "screenshot":      self.screenshot_path,
            "step_results":    serialised,
            "steps":           serialised,
            "discrepancies":   discrepancies,
            "discrepancy_counts": {
                "missing_recorded_fields": missing_count,
                "new_unexpected_fields": new_count,
                "renamed_recorded_fields": renamed_count,
                "healed_selector_matches": healed_count,
                "recorded_fields_not_filled": not_filled_count,
                "warnings": warning_count,
                "total": len(discrepancies),
            },
            "severity_counts": {
                "blocker": blocker_count,
                "warning": warning_count,
                "info": info_count,
                "total": len(discrepancies),
            },
            "replay_mode": mode,
            "replay_config": {
                "mode": mode,
                "fail_on_missing_required_fields": bool(self.fail_on_missing_required_fields),
                "fail_on_new_required_fields": bool(self.fail_on_new_required_fields),
                "fail_on_not_filled_fields": bool(self.fail_on_not_filled_fields),
            },
            "qa_outcome": qa_outcome,
            "qa_summary": qa_summary,
            "run_metrics": run_metrics,
            "instability_indicators": instability_indicators,
            "trend_ready": trend_ready,
            "pages_compared": self.pages_compared,
            "fields_scanned": fields_scanned,
            "data_generation": dict(self.data_generation or {}),
            "runtime_data": dict(self.runtime_data or {}),
            "excel_report_path": self.excel_report_path,
            "master_data_path": self.master_data_path,
            "master_data_run_id": self.master_data_run_id,
            "duration":        self.duration_seconds,
        }
