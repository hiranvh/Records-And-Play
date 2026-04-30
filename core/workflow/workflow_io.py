"""Workflow file persistence, validation, and compaction helpers."""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

from .normalizer import deduplicate_steps, normalize_workflow_steps

logger = logging.getLogger(__name__)

WORKFLOWS_DIR = os.path.join(os.getcwd(), "workflows")
SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = frozenset({2, 3})
REQUIRED_STEP_KEYS = (
    "type",
    "tag",
    "id",
    "name",
    "label",
    "value",
    "selector",
    "input_type",
)


def _normalize_step_schema(step: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure required workflow step keys are always present as strings."""
    out = dict(step or {})
    for key in REQUIRED_STEP_KEYS:
        value = out.get(key, "")
        if value is None:
            value = ""
        out[key] = value if isinstance(value, str) else str(value)
    return out


def _coerce_schema_version(raw: Any) -> int:
    """Resolve the workflow schema version while staying backward compatible."""
    try:
        version = int(raw)
    except (TypeError, ValueError):
        return 2

    if version in SUPPORTED_SCHEMA_VERSIONS:
        return version

    logger.warning(f"Unknown workflow schema_version '{raw}', preserving workflow payload")
    return version


def _normalize_page_field_schema(field: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a recorded field snapshot while preserving additive metadata."""
    out = dict(field or {})

    for key in (
        "tag",
        "input_type",
        "id",
        "name",
        "label",
        "placeholder",
        "selector",
        "aria_label",
        "section",
    ):
        value = out.get(key, "")
        if value is None:
            value = ""
        out[key] = value if isinstance(value, str) else str(value)

    for key in ("required", "readonly", "visible", "chosen"):
        if key in out:
            out[key] = bool(out.get(key))

    nearby_text = out.get("nearby_text", [])
    if not isinstance(nearby_text, list):
        nearby_text = []
    out["nearby_text"] = [str(item).strip() for item in nearby_text if str(item or "").strip()]

    options = out.get("options", [])
    normalised_options: List[Dict[str, str]] = []
    if isinstance(options, list):
        for option in options:
            if not isinstance(option, dict):
                continue
            normalised_options.append({
                "text": str(option.get("text", "") or ""),
                "value": str(option.get("value", "") or ""),
            })
    out["options"] = normalised_options
    return out


def _normalize_page_checkpoint_schema(checkpoint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize one page checkpoint while preserving unknown additive keys."""
    if not isinstance(checkpoint, dict):
        return None

    out = dict(checkpoint)
    for key in ("page_id", "url", "path", "title", "signature"):
        value = out.get(key, "")
        if value is None:
            value = ""
        out[key] = value if isinstance(value, str) else str(value)

    headings = out.get("headings", [])
    if not isinstance(headings, list):
        headings = []
    out["headings"] = [str(item).strip() for item in headings if str(item or "").strip()]

    fields = out.get("fields", [])
    normalised_fields: List[Dict[str, Any]] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                continue
            normalised_fields.append(_normalize_page_field_schema(field))
    out["fields"] = normalised_fields
    out["field_count"] = len(normalised_fields)

    captured_at = out.get("captured_at", 0)
    try:
        out["captured_at"] = int(captured_at or 0)
    except (TypeError, ValueError):
        out["captured_at"] = 0

    return out


def _normalize_page_checkpoints(checkpoints: Any) -> List[Dict[str, Any]]:
    """Normalize page checkpoints while preserving additive metadata."""
    if not isinstance(checkpoints, list):
        return []

    normalised: List[Dict[str, Any]] = []
    for checkpoint in checkpoints:
        norm = _normalize_page_checkpoint_schema(checkpoint)
        if norm is not None:
            normalised.append(norm)
    return normalised


def load_workflow(name: str) -> Optional[Dict[str, Any]]:
    """Load a workflow file with validation."""
    # Resolve path
    if not name.endswith(".json"):
        name += ".json"
    
    path = os.path.join(WORKFLOWS_DIR, name)
    
    # Try case-insensitive match
    if not os.path.exists(path):
        path = _find_case_insensitive(name)
        if not path:
            logger.warning(f"Workflow not found: {name}")
            return None
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Validate structure
        if not isinstance(data, dict):
            logger.error(f"Invalid workflow format: {name}")
            return None
        
        if "steps" not in data:
            logger.error(f"Workflow missing 'steps' array: {name}")
            return None
        
        # Ensure steps is a list
        if not isinstance(data["steps"], list):
            logger.error(f"Workflow 'steps' must be an array: {name}")
            return None

        data["schema_version"] = _coerce_schema_version(data.get("schema_version"))

        recording_metadata = data.get("recording_metadata")
        embedded_page_checkpoints = []
        if isinstance(recording_metadata, dict):
            embedded_page_checkpoints = recording_metadata.get("page_checkpoints", [])

        if "page_checkpoints" in data or embedded_page_checkpoints:
            data["page_checkpoints"] = _normalize_page_checkpoints(
                data.get("page_checkpoints", embedded_page_checkpoints)
            )

        # Sanitize known transient artifacts from older recordings while
        # preserving intended datepicker day clicks and other interactions.
        data["steps"] = _sanitize_loaded_steps(data.get("steps", []))
        
        return data
    
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in workflow {name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to load workflow {name}: {e}")
        return None


def save_workflow(
    url: str,
    steps: List[Dict[str, Any]],
    name: str,
    recording_metadata: Optional[Dict[str, Any]] = None,
    page_checkpoints: Optional[List[Dict[str, Any]]] = None,
    schema_version: Optional[int] = None,
) -> bool:
    """Save workflow to JSON file."""
    if not name.endswith(".json"):
        name += ".json"
    
    os.makedirs(WORKFLOWS_DIR, exist_ok=True)
    path = os.path.join(WORKFLOWS_DIR, name)
    
    normalized_steps = [_normalize_step_schema(step) for step in (steps or [])]
    metadata = dict(recording_metadata or {})
    embedded_page_checkpoints = metadata.pop("page_checkpoints", None)
    if page_checkpoints is None and isinstance(embedded_page_checkpoints, list):
        page_checkpoints = embedded_page_checkpoints

    normalized_checkpoints = _normalize_page_checkpoints(page_checkpoints or [])

    if metadata:
        metadata.setdefault("page_checkpoint_count", len(normalized_checkpoints))

    resolved_schema_version = _coerce_schema_version(schema_version)
    if schema_version is None:
        resolved_schema_version = SCHEMA_VERSION

    workflow = {
        "schema_version": resolved_schema_version,
        "recorded_at": int(datetime.now().timestamp()),
        "url": url,
        "start_url": url,
        "steps": normalized_steps,
    }
    
    if resolved_schema_version >= 3:
        workflow["page_checkpoints"] = normalized_checkpoints

    if metadata:
        workflow["recording_metadata"] = metadata
    
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(workflow, f, indent=4, ensure_ascii=False)
        
        logger.info(f"Saved workflow: {path} ({len(normalized_steps)} steps)")
        return True
    
    except Exception as e:
        logger.error(f"Failed to save workflow {name}: {e}")
        return False


def rename_workflow(old_name: str, new_name: str, overwrite: bool = False) -> Dict[str, Any]:
    """Rename a workflow file safely."""
    if not old_name.endswith(".json"):
        old_name += ".json"
    if not new_name.endswith(".json"):
        new_name += ".json"
    
    old_path = os.path.join(WORKFLOWS_DIR, old_name)
    new_path = os.path.join(WORKFLOWS_DIR, new_name)
    
    # Check source exists
    if not os.path.exists(old_path):
        return {"success": False, "error": f"Workflow not found: {old_name}"}
    
    # Check target doesn't exist (unless overwrite)
    if os.path.exists(new_path) and not overwrite:
        return {"success": False, "error": f"Workflow already exists: {new_name}"}
    
    try:
        os.rename(old_path, new_path)
        return {"success": True, "message": f"Renamed '{old_name}' to '{new_name}'"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def compact_workflow(name: str) -> Dict[str, Any]:
    """Clean and optimize a workflow file."""
    workflow = load_workflow(name)
    if not workflow:
        return {"success": False, "error": "Workflow not found"}
    
    steps = workflow.get("steps", [])
    before_count = len(steps)
    
    optimized = deduplicate_steps(steps)
    
    # Apply normalization
    normalized = normalize_workflow_steps(optimized)
    
    after_count = len(normalized)
    
    # Save optimized workflow
    workflow["steps"] = normalized
    save_workflow(
        url=workflow.get("url", ""),
        steps=normalized,
        name=name,
        recording_metadata=workflow.get("recording_metadata"),
        page_checkpoints=workflow.get("page_checkpoints"),
        schema_version=workflow.get("schema_version"),
    )
    
    return {
        "success": True,
        "message": f"Optimized: {before_count} → {after_count} steps",
        "before": before_count,
        "after": after_count,
    }


def _sanitize_loaded_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove non-actionable transient steps that commonly cause false replay failures."""
    cleaned: List[Dict[str, Any]] = []

    for step in steps or []:
        step_type = (step.get("type") or "").lower().strip()
        selector = (step.get("selector") or "").lower()
        has_identity = any((step.get(k) or "").strip() for k in ("id", "name", "label", "text"))

        # Datepicker year/month select widgets are transient overlays and often
        # disappear before replay reaches them. Keep explicit date day clicks.
        if step_type == "select" and "ui-datepicker-div" in selector:
            if not has_identity:
                continue

        # Chosen openers and accordion handles are helper clicks for the next
        # actionable select/click step, not standalone replay actions.
        if step_type in ("click", "click_link") and not has_identity:
            if any(token in selector for token in ("chzn-single", "chosen-single", "a.handle.sub-theme-bg")):
                continue

        cleaned.append(step)

    return cleaned


def _find_case_insensitive(name: str) -> Optional[str]:
    """Find workflow file case-insensitively."""
    if not os.path.exists(WORKFLOWS_DIR):
        return None
    
    name_lower = name.lower()
    for filename in os.listdir(WORKFLOWS_DIR):
        if filename.lower() == name_lower:
            return os.path.join(WORKFLOWS_DIR, filename)
    
    return None


def validate_workflow(name: str) -> Dict[str, Any]:
    """Validate a workflow and return report."""
    workflow = load_workflow(name)
    if not workflow:
        return {
            "valid": False,
            "errors": ["Workflow file not found or invalid"],
            "warnings": [],
            "suggestions": [],
        }
    
    steps = workflow.get("steps", [])
    errors = []
    warnings = []
    suggestions = []
    
    if not steps:
        errors.append("Workflow has 0 steps")
        return {"valid": False, "errors": errors, "warnings": warnings, "suggestions": suggestions}
    
    # Check for issues
    noise_count = 0
    brittle_count = 0
    empty_clicks = 0
    
    for step in steps:
        step_type = (step.get("type") or "").lower()
        step_id = (step.get("id") or "").lower()
        selector = step.get("selector", "")
        text = step.get("text", "")
        
        # Noise container clicks
        if step_type in ("click", "click_link") and step_id in {
            "employeeaddiv", "divsidemenuouter", "login"
        }:
            noise_count += 1
        
        # Brittle selectors
        if "nth-of-type" in selector and not step.get("id"):
            brittle_count += 1
        
        # Empty label clicks
        if "click" in step_type and not text and not step.get("label"):
            empty_clicks += 1
    
    if noise_count > 0:
        warnings.append(f"{noise_count} noise container click(s) detected")
        suggestions.append("Re-record avoiding clicks on page containers")
    
    if brittle_count > 3:
        warnings.append(f"{brittle_count} steps use brittle nth-of-type selectors")
        suggestions.append("Consider re-recording with more specific targets")
    
    if empty_clicks > 0:
        suggestions.append(f"{empty_clicks} click(s) have no text label - may be unreliable")
    
    actionable = [s for s in steps if s.get("type") in ("input", "select", "click", "click_link")]
    if len(actionable) < 2:
        errors.append("Workflow has fewer than 2 actionable steps")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "step_count": len(steps),
        "actionable_steps": len(actionable),
    }


def get_workflow_path(name: str) -> Optional[str]:
    """Get full path to workflow file."""
    if not name.endswith(".json"):
        name += ".json"
    
    path = os.path.join(WORKFLOWS_DIR, name)
    if os.path.exists(path):
        return path
    
    return _find_case_insensitive(name)
