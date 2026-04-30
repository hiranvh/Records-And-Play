"""Workflow-step normalization helpers for recorded JSON artifacts."""

import re
from typing import List, Dict, Any, Optional

from core.utils import normalize_text


# Known noise container IDs to filter out
NOISE_CONTAINER_IDS = {
    "employeeaddiv",
    "divsidemenuouter",
    "div_enrollmentsummarydet",
    "div_accodianhead_1",
    "div_showplans_1",
    "divcobselect",
    "divtotalbtn",
    "enrollsummarywrap",
    "login",  # Container div, not the button
}


def normalize_step(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize a single raw recorded step.
    Returns cleaned step dict or None if should be filtered.
    """
    if not isinstance(step, dict):
        return None
    
    normalized = dict(step)
    step_type = (normalized.get("type") or "").lower()
    step_id = normalized.get("id", "") or ""
    selector = normalized.get("selector", "") or ""
    text = (normalized.get("text") or "").strip()
    label = (normalized.get("label") or "").strip()
    
    # Filter noise container clicks
    if step_type in ("click", "click_link"):
        if step_id.lower() in NOISE_CONTAINER_IDS:
            return None
        
        # Filter clicks on Chosen.js containers without text (these are just opens)
        if ("_chzn" in selector.lower() or "_chosen" in selector.lower()) and not text:
            # Keep only if it's a select operation (will be merged later)
            return None
    
    # Filter datepicker sub-steps (will be merged into date inputs)
    if "ui-datepicker-div" in selector.lower():
        if step_type in ("select", "click", "click_link"):
            return None
    
    # Build normalized step
    normalized.update({
        "type": step_type,
        "tag": step.get("tag", ""),
        "id": step_id,
        "name": step.get("name", ""),
        "label": label,
        "text": text,
        "value": step.get("value", ""),
        "selector": selector,
        "input_type": step.get("input_type", ""),
        "placeholder": step.get("placeholder", ""),
    })
    
    return normalized


def normalize_workflow_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Full normalization pipeline:
    1. Normalize individual steps
    2. Deduplicate consecutive steps
    3. Merge Chosen.js pairs
    4. Merge datepicker sub-steps
    5. Filter noise clicks
    """
    if not steps:
        return []
    
    # Step 1: Normalize individual steps
    normalized = []
    for step in steps:
        norm = normalize_step(step)
        if norm is not None:
            normalized.append(norm)
    
    # Step 2: Deduplicate consecutive identical steps
    normalized = deduplicate_steps(normalized)
    
    # Step 3: Merge Chosen.js pairs (open + select → single select)
    normalized = _merge_chosen_pairs(normalized)
    
    # Step 4: Merge datepicker sub-steps
    normalized = _merge_datepicker_steps(normalized)
    
    return normalized


def deduplicate_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove consecutive duplicate steps while preserving the latest input value."""
    if not steps:
        return []
    
    result = [steps[0]]
    
    for step in steps[1:]:
        last = result[-1]
        
        # Check if same target
        same_type = step.get("type") == last.get("type")
        same_id = step.get("id") and step.get("id") == last.get("id")
        same_name = step.get("name") and step.get("name") == last.get("name")
        same_selector = step.get("selector") and step.get("selector") == last.get("selector")
        
        is_same = same_type and (same_id or same_name or same_selector)
        
        if is_same:
            # For input/select, keep latest value
            if step.get("type") in ("input", "select"):
                result[-1] = step
            # For clicks, skip duplicate
            elif "click" in (step.get("type") or ""):
                continue
            else:
                result.append(step)
        else:
            result.append(step)
    
    return result


def _merge_chosen_pairs(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge Chosen.js open click + option select into single select step.
    Pattern: click_link on X_chzn → select on X
    """
    merged = []
    skip_next = False
    
    for i, step in enumerate(steps):
        if skip_next:
            skip_next = False
            continue
        
        # Check if this is a Chosen.js container click
        selector = step.get("selector", "")
        if ("_chzn" in selector.lower() or "_chosen" in selector.lower()):
            # Look ahead for corresponding select step
            if i + 1 < len(steps):
                next_step = steps[i + 1]
                next_selector = next_step.get("selector", "")
                
                # Check if next step selects from the same base ID
                if next_step.get("type") == "select":
                    # Extract base ID from Chosen selector
                    base_match = re.search(r'#([^_\s]+)_chzn', selector) or re.search(r'#([^_\s]+)_chosen', selector)
                    if base_match:
                        base_id = base_match.group(1)
                        if base_id in next_selector or base_id == next_step.get("id"):
                            # Merge: create single select step
                            merged_select = {
                                **next_step,
                                "text": step.get("text", "") or next_step.get("text", ""),
                            }
                            merged.append(merged_select)
                            skip_next = True
                            continue
        
        merged.append(step)
    
    return merged


def _merge_datepicker_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge datepicker sub-steps (month/year select + day click) into date input step.
    Pattern: input on date field → select on ui-datepicker-div → click on ui-datepicker-div
    """
    merged = []
    i = 0
    
    while i < len(steps):
        step = steps[i]
        
        # Check if this is a date input
        if step.get("placeholder") == "MM/DD/YYYY" or step.get("type") == "input":
            # Look ahead for datepicker sub-steps
            date_parts = {"month": None, "year": None, "day": None}
            j = i + 1
            
            while j < len(steps) and j <= i + 5:
                future = steps[j]
                future_selector = future.get("selector", "")
                
                if "ui-datepicker-div" not in future_selector.lower():
                    break
                
                if future.get("type") == "select":
                    value = future.get("value", "")
                    if value.isdigit():
                        if len(value) == 4:
                            date_parts["year"] = value
                        elif len(value) <= 2:
                            date_parts["month"] = str(int(value) + 1)  # 0-indexed
                
                elif "click" in (future.get("type") or ""):
                    day_text = future.get("text", "").strip()
                    if day_text.isdigit():
                        date_parts["day"] = day_text
                
                j += 1
            
            # If we found date parts, construct date string
            if any(date_parts.values()):
                # Build date string from parts
                year = date_parts["year"] or "2024"
                month = date_parts["month"] or "01"
                day = date_parts["day"] or "01"
                
                date_step = {
                    **step,
                    "value": f"{month}/{day}/{year}",
                    "type": "input",
                }
                merged.append(date_step)
                i = j  # Skip consumed sub-steps
                continue
        
        merged.append(step)
        i += 1
    
    return merged


def is_noise_click(step: Dict[str, Any]) -> bool:
    """Check if a step is a noise container click."""
    if "click" not in (step.get("type") or "").lower():
        return False
    
    step_id = (step.get("id") or "").lower()
    return step_id in NOISE_CONTAINER_IDS


def simplify_selector(selector: str) -> str:
    """
    Simplify complex nth-of-type selectors to more robust versions.
    Returns simplified selector or original if can't simplify.
    """
    if "nth-of-type" not in selector:
        return selector
    
    try:
        # Try to extract ID anchors
        id_matches = re.findall(r'#([a-zA-Z_][\w-]*)', selector)
        if id_matches:
            return f"#{id_matches[-1]}"
        
        # Try to extract meaningful tag chain
        tags = re.findall(r'([a-z]+)(?=:nth-of-type|>)', selector)
        if len(tags) >= 2:
            return f"{tags[-2]} {tags[-1]}"
    
    except Exception:
        pass
    
    return selector
