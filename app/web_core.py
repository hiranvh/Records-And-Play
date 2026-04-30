"""
web_core.py
-----------
Synchronous facade for the web application.
Clean, simple, and reliable.
"""

import logging
from typing import Dict, Any, Optional, Tuple, List, Set

from core.constants import stop_execution_event
from core.profile import generate_profile
from playback import run_execution_mode as run_shared_execution_mode
from recorder import (
    start_teaching_mode as start_shared_teaching_mode,
    get_last_recording_metadata,
)
from core.workflow import (
    save_workflow as save_shared_workflow,
    load_workflow as load_shared_workflow,
    rename_workflow as rename_shared_workflow,
    compact_workflow as compact_shared_workflow,
    validate_workflow as validate_shared_workflow,
)

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any, default: bool) -> bool:
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


def _extract_replay_config(override_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = override_data if isinstance(override_data, dict) else {}
    mode = str(
        payload.get("replay_mode")
        or payload.get("qa_replay_mode")
        or "standard"
    ).strip().lower()
    if mode not in {"lenient", "standard", "strict"}:
        mode = "standard"

    return {
        "mode": mode,
        "fail_on_missing_required_fields": _coerce_bool(
            payload.get("fail_on_missing_required_fields"),
            default=True,
        ),
        "fail_on_new_required_fields": _coerce_bool(
            payload.get("fail_on_new_required_fields"),
            default=False,
        ),
        "fail_on_not_filled_fields": _coerce_bool(
            payload.get("fail_on_not_filled_fields"),
            default=True,
        ),
    }


def _estimate_fields_scanned_from_workflow(workflow: Dict[str, Any], pages_compared: int) -> int:
    checkpoints = workflow.get("page_checkpoints") if isinstance(workflow, dict) else []
    if not isinstance(checkpoints, list) or not checkpoints:
        return 0

    total_fields = 0
    valid_checkpoints = 0
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            continue
        valid_checkpoints += 1
        field_count = checkpoint.get("field_count")
        if isinstance(field_count, int):
            total_fields += max(0, field_count)
            continue
        fields = checkpoint.get("fields")
        if isinstance(fields, list):
            total_fields += len([f for f in fields if isinstance(f, dict)])

    if valid_checkpoints <= 0:
        return 0
    if pages_compared <= 0:
        return 0
    if pages_compared >= valid_checkpoints:
        return total_fields

    avg = total_fields / float(valid_checkpoints)
    return max(0, int(round(avg * pages_compared)))


def _augment_operational_metrics(result: Dict[str, Any], workflow: Dict[str, Any]) -> None:
    if not isinstance(result, dict):
        return

    qa_summary = result.get("qa_summary") if isinstance(result.get("qa_summary"), dict) else {}
    discrepancy_counts = result.get("discrepancy_counts") if isinstance(result.get("discrepancy_counts"), dict) else {}
    severity_counts = result.get("severity_counts") if isinstance(result.get("severity_counts"), dict) else {}
    run_metrics = result.get("run_metrics") if isinstance(result.get("run_metrics"), dict) else {}

    pages_compared = int(run_metrics.get("pages_compared", result.get("pages_compared", 0)) or 0)
    fields_scanned = int(run_metrics.get("fields_scanned", result.get("fields_scanned", 0)) or 0)
    if fields_scanned <= 0:
        fields_scanned = _estimate_fields_scanned_from_workflow(workflow, pages_compared)

    healed_matches = int(run_metrics.get("healed_matches", qa_summary.get("healed_matches", discrepancy_counts.get("healed_selector_matches", 0))) or 0)
    warnings = int(run_metrics.get("warnings", qa_summary.get("warnings", discrepancy_counts.get("warnings", 0))) or 0)
    blockers = int(run_metrics.get("blockers", qa_summary.get("blockers", severity_counts.get("blocker", 0))) or 0)
    runtime_duration_seconds = float(run_metrics.get("runtime_duration_seconds", result.get("duration", 0.0)) or 0.0)

    resolved_metrics = {
        "pages_compared": pages_compared,
        "fields_scanned": fields_scanned,
        "healed_matches": healed_matches,
        "warnings": warnings,
        "blockers": blockers,
        "runtime_duration_seconds": runtime_duration_seconds,
    }
    result["run_metrics"] = resolved_metrics
    result["fields_scanned"] = fields_scanned

    if not isinstance(result.get("trend_ready"), dict):
        result["trend_ready"] = {
            "schema": "qa_run_metrics_v1",
            "replay_mode": str(result.get("replay_mode") or qa_summary.get("replay_mode") or "standard"),
            "qa_outcome": str(result.get("qa_outcome") or ""),
            "run_metrics": dict(resolved_metrics),
            "severity_counts": {
                "blocker": blockers,
                "warning": warnings,
                "info": int(qa_summary.get("info", severity_counts.get("info", 0)) or 0),
            },
            "discrepancy_counts": {
                "missing_fields": int(qa_summary.get("missing_fields", discrepancy_counts.get("missing_recorded_fields", 0)) or 0),
                "new_fields": int(qa_summary.get("new_fields", discrepancy_counts.get("new_unexpected_fields", 0)) or 0),
                "healed_matches": healed_matches,
                "not_filled": int(qa_summary.get("not_filled", discrepancy_counts.get("recorded_fields_not_filled", 0)) or 0),
            },
        }


def _derive_qa_outcome(result: Dict[str, Any], finished: bool, failed_steps: int) -> str:
    """Resolve QA-friendly replay outcome from additive result metadata."""
    explicit = str(result.get("qa_outcome") or "").strip()
    if explicit in {"Passed", "Passed with warnings", "Failed"}:
        return explicit

    summary = result.get("qa_summary") if isinstance(result.get("qa_summary"), dict) else {}
    discrepancy_counts = result.get("discrepancy_counts") if isinstance(result.get("discrepancy_counts"), dict) else {}

    blockers = int(summary.get("blockers", 0) or 0)
    warnings = int(summary.get("warnings", 0) or 0)

    # Backward compatibility path for payloads that do not yet provide qa_summary.
    if not summary:
        warnings = int(discrepancy_counts.get("warnings", 0) or 0)
        blockers += int(discrepancy_counts.get("missing_recorded_fields", 0) or 0)
        blockers += int(discrepancy_counts.get("recorded_fields_not_filled", 0) or 0)

    if not finished or failed_steps > 0 or blockers > 0:
        return "Failed"
    if warnings > 0:
        return "Passed with warnings"
    return "Passed"


def validate_workflow(name: str = "workflow.json") -> Dict[str, Any]:
    """Validate a workflow file and return report."""
    return validate_shared_workflow(name)


def _build_execution_profile(override_data: Optional[Dict] = None, workflow_name: str = "") -> Dict[str, Any]:
    """
    Build a deterministic execution profile.

    Explicit overrides always win, but we also derive the correlated fields the
    replay engine expects, such as county/city/state/billing_location and
    employee_class from ZIP.
    """
    overrides = dict(override_data or {})
    lower = {str(k).lower().strip(): v for k, v in overrides.items()}
    if workflow_name:
        lower.setdefault("_workflow_name", workflow_name)
    profile = generate_profile(override_data_normalized=lower)
    for key, value in overrides.items():
        if value not in (None, ""):
            profile[key] = value

    # Add common aliases without inventing missing data.
    if "group_name" in profile:
        profile.setdefault("group", profile["group_name"])
    if "group" in profile:
        profile.setdefault("group_name", profile["group"])
    if "dob" not in profile and lower.get("dob_date"):
        profile["dob"] = overrides.get("dob_date")
    if workflow_name:
        profile.setdefault("_workflow_name", workflow_name)
        profile.setdefault("workflow_name", workflow_name)

    return profile


def run_execution_mode(
    url: Optional[str] = None,
    override_data: Optional[Dict[str, Any]] = None,
    headless: bool = False,
    workflow_name: str = "workflow.json",
    update_callback=None,
    group_name: Optional[str] = None,
) -> Tuple[bool, str, Optional[str], List[Dict[str, Any]]]:
    """
    Execute a workflow with robust error handling.
    
    Returns:
        (success, message, screenshot_path, step_results)
    """
    # Always start a new run with a clean stop state.
    # Without this, a previous /api/stop request can cancel the next replay immediately.
    try:
        stop_execution_event.clear()
    except Exception:
        pass

    # Load workflow
    wf = load_shared_workflow(workflow_name)
    if not wf:
        return False, "Workflow not found", None, []
    
    # Resolve URL
    target_url = url or wf.get("start_url") or wf.get("url") or "https://www.google.com"
    
    replay_config = _extract_replay_config(override_data)

    # Build execution profile
    execution_profile = _build_execution_profile(override_data or {}, workflow_name=workflow_name)
    execution_profile.setdefault("start_url", target_url)
    execution_profile["_replay_mode"] = replay_config["mode"]
    execution_profile["_fail_on_missing_required_fields"] = replay_config["fail_on_missing_required_fields"]
    execution_profile["_fail_on_new_required_fields"] = replay_config["fail_on_new_required_fields"]
    execution_profile["_fail_on_not_filled_fields"] = replay_config["fail_on_not_filled_fields"]

    # Group-aware fallback values for dependent selects.
    if group_name:
        execution_profile.setdefault("group_name", group_name)
        execution_profile.setdefault("group", group_name)
        execution_profile.setdefault("billing_location", group_name)

    # Keep employee class deterministic even when omitted by overrides.
    if not str(execution_profile.get("employee_class") or "").strip():
        execution_profile["employee_class"] = "Retiree"
    
    # Execute with the step engine
    result = run_shared_execution_mode(
        workflow_data=wf,
        execution_profile=execution_profile,
        headless=headless,
        override_url=target_url,
        update_callback=update_callback,
        group_name=group_name,
    )
    
    finished = result.get("status") == "finished"
    failed_steps = int(result.get("steps_failed", 0) or 0)
    executed_steps = int(result.get("steps_executed", 0) or 0)
    skipped_steps = int(result.get("steps_skipped", 0) or 0)
    qa_metadata_available = any(
        key in result for key in ("discrepancies", "discrepancy_counts", "qa_summary", "qa_outcome")
    )
    if qa_metadata_available and "replay_mode" not in result:
        result["replay_mode"] = replay_config["mode"]

    if qa_metadata_available:
        _augment_operational_metrics(result, wf)

    qa_outcome = _derive_qa_outcome(result, finished=finished, failed_steps=failed_steps) if qa_metadata_available else ""

    success = (
        qa_outcome in {"Passed", "Passed with warnings"}
        if qa_metadata_available
        else (finished and failed_steps == 0)
    )

    if qa_metadata_available:
        try:
            from playback.excel_reporter import export_discrepancy_reports

            mode_label = str(result.get("replay_mode") or replay_config["mode"])
            report_workflow_name = f"{workflow_name} [{mode_label}]"
            report_paths = export_discrepancy_reports(result, workflow_name=report_workflow_name)
            if report_paths:
                result["qa_report_paths"] = report_paths
                try:
                    from playback.master_data_reporter import update_master_excel_report_name

                    update_master_excel_report_name(
                        workbook_path=str(result.get("master_data_path") or ""),
                        run_id=str(result.get("master_data_run_id") or ""),
                        excel_report_path=str(report_paths.get("excel") or ""),
                    )
                except Exception as update_exc:
                    logger.warning(f"Could not update master data report link: {update_exc}")
        except Exception as exc:
            logger.warning(f"Could not export discrepancy reports: {exc}")

    summary = f"finished={finished}, executed={executed_steps}, skipped={skipped_steps}, failed={failed_steps}"

    if result.get("error"):
        message = result.get("error")
    elif qa_metadata_available:
        message = qa_outcome
    else:
        message = "Finished" if success else f"Finished with failures ({summary})"

    return (
        success,
        message,
        result.get("screenshot_path") or result.get("screenshot"),
        result.get("step_results") or result.get("steps", []),
    )


def start_teaching_mode(url: str, workflow_name: str = "workflow.json", update_callback=None):
    """Start recording browser interactions."""
    return start_shared_teaching_mode(url, workflow_name, update_callback=update_callback)


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _is_executable_step(step_type: str) -> bool:
    return _norm_text(step_type) in {"input", "select", "toggle", "date"}


def _is_visible_checkpoint_field(field: Dict[str, Any]) -> bool:
    if not isinstance(field, dict):
        return False
    if not bool(field.get("visible", True)):
        return False

    tag = _norm_text(field.get("tag"))
    input_type = _norm_text(field.get("input_type"))
    if tag == "input" and input_type in {"hidden", "submit", "button", "file", "image", "reset"}:
        return False
    return True


def _build_field_identity_tokens(item: Dict[str, Any]) -> Set[str]:
    tokens: Set[str] = set()
    for key in ("id", "name", "label", "placeholder"):
        value = _norm_text(item.get(key))
        if value:
            tokens.add(f"{key}:{value}")
    return tokens


def _identity_display(field: Dict[str, Any]) -> str:
    for key in ("label", "id", "name", "placeholder"):
        value = str(field.get(key) or "").strip()
        if value:
            return value
    return "(unnamed field)"


def _run_recorder_quality_audit(
    steps: List[Dict[str, Any]],
    page_checkpoints: List[Dict[str, Any]],
) -> Dict[str, Any]:
    executable_tokens: Set[str] = set()
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if not _is_executable_step(str(step.get("type") or "")):
            continue
        executable_tokens.update(_build_field_identity_tokens(step))

    missing_fields: List[Dict[str, Any]] = []
    seen_missing: Set[str] = set()

    for checkpoint in page_checkpoints or []:
        if not isinstance(checkpoint, dict):
            continue

        page_id = str(checkpoint.get("page_id") or "")
        page_url = str(checkpoint.get("url") or "")
        fields = checkpoint.get("fields")
        if not isinstance(fields, list):
            continue

        for field in fields:
            if not _is_visible_checkpoint_field(field):
                continue

            tokens = _build_field_identity_tokens(field)
            if not tokens:
                continue
            if any(token in executable_tokens for token in tokens):
                continue

            key = "|".join(sorted(tokens))
            scoped_key = f"{page_id}|{page_url}|{key}"
            if scoped_key in seen_missing:
                continue
            seen_missing.add(scoped_key)

            missing_fields.append(
                {
                    "page_id": page_id,
                    "page_url": page_url,
                    "label": str(field.get("label") or ""),
                    "id": str(field.get("id") or ""),
                    "name": str(field.get("name") or ""),
                    "placeholder": str(field.get("placeholder") or ""),
                    "required": bool(field.get("required", False)),
                    "display": _identity_display(field),
                }
            )

    preview_limit = 40
    missing_preview = missing_fields[:preview_limit]
    has_missing = bool(missing_fields)
    required_missing_count = len([f for f in missing_fields if bool(f.get("required"))])

    return {
        "has_missing_fields": has_missing,
        "missing_count": len(missing_fields),
        "required_missing_count": required_missing_count,
        "missing_fields": missing_preview,
        "truncated": len(missing_fields) > preview_limit,
    }


def save_workflow(
    url: str,
    steps: List[Dict[str, Any]],
    name: str,
    force_save: bool = False,
) -> Dict[str, Any]:
    """Save a workflow with recorder quality audit warnings."""
    metadata = get_last_recording_metadata()
    checkpoints = metadata.get("page_checkpoints") if isinstance(metadata, dict) else []
    if not isinstance(checkpoints, list):
        checkpoints = []

    audit = _run_recorder_quality_audit(steps=steps or [], page_checkpoints=checkpoints)
    if audit.get("has_missing_fields") and not force_save:
        missing_count = int(audit.get("missing_count", 0) or 0)
        req_missing = int(audit.get("required_missing_count", 0) or 0)
        message = (
            f"Recorder quality audit: {missing_count} visible field(s) from checkpoints are missing executable steps"
        )
        if req_missing > 0:
            message += f" ({req_missing} required)."
        else:
            message += "."

        return {
            "success": False,
            "saved": False,
            "status": "warning",
            "message": message,
            "audit": audit,
        }

    ok = save_shared_workflow(url, steps, name, recording_metadata=metadata)
    if ok:
        return {
            "success": True,
            "saved": True,
            "status": "success",
            "message": f"Workflow saved as '{name}'",
            "audit": audit,
        }

    return {
        "success": False,
        "saved": False,
        "status": "error",
        "message": "Failed to save workflow",
        "audit": audit,
    }


def compact_workflow(name: str) -> Dict[str, Any]:
    """Clean and optimize a workflow file."""
    return compact_shared_workflow(name)


def rename_workflow(old_name: str, new_name: str, overwrite: bool = False) -> Dict[str, Any]:
    """Rename a workflow file."""
    return rename_shared_workflow(old_name, new_name, overwrite=overwrite)
