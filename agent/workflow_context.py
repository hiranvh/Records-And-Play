"""
agent.workflow_context
----------------------
Provides enrollment-step context helpers for the autonomous agent.

These helpers are only used by the agent-side LLM flow. They do not belong
to shared workflow storage or playback execution.
"""

import json
from typing import Dict, List

from playwright.sync_api import Page


def detect_enrollment_step(page: Page, page_num: int = 0) -> Dict:
    """
    Analyze page content to determine which step of enrollment we're on.

    Returns:
        {
            "step": "coverage_selection",
            "flow_type": "dual_enrollment",
            "keywords_found": ["coverage", "dual", "group"],
            "confidence": 0.85,
        }
    """
    try:
        page_text = (
            page.title() + " " + page.url + " "
            + page.locator("body").evaluate("el => el.innerText")
        )
        page_lower = page_text.lower()
    except Exception:
        page_lower = ""

    step_patterns = {
        "login": ["login", "password", "sign in"],
        "employee_profile": ["personal", "name", "dob", "ssn", "hire date"],
        "coverage_selection": ["coverage", "medical", "dental", "vision", "coverage type"],
        "plan_selection": ["plan", "select plan", "plan option", "hmo", "ppo"],
        "beneficiary_setup": ["beneficiary", "dependent", "spouse", "child"],
        "health_questions": ["health", "medical history", "pre-existing", "tobacco"],
        "final_review": ["review", "confirm", "summary", "enrollment summary"],
        "confirmation": ["confirmed", "success", "congratulation", "enrolled"],
    }

    is_dual = any(keyword in page_lower for keyword in ["dual", "two group", "multiple group", "secondary"])

    step_scores = {}
    for step, keywords in step_patterns.items():
        score = sum(1 for keyword in keywords if keyword in page_lower) / len(keywords)
        step_scores[step] = score

    best_step = max(step_scores, key=step_scores.get)
    confidence = step_scores[best_step]

    keywords_found = [
        keyword
        for step, keywords in step_patterns.items()
        for keyword in keywords
        if keyword in page_lower
    ]

    return {
        "step": best_step,
        "flow_type": "dual_enrollment" if is_dual else "single_enrollment",
        "keywords_found": keywords_found[:10],
        "confidence": min(confidence, 1.0),
        "page_num": page_num,
    }


def get_workflow_context_prompt(page: Page, profile: Dict, page_num: int = 0) -> str:
    """
    Generate enrollment context instructions for the LLM.
    Tell it exactly what workflow we're in.
    """
    step_info = detect_enrollment_step(page, page_num)

    page_title = page.title()
    page_url = page.url

    prompt = f"""
### ENROLLMENT WORKFLOW CONTEXT

**Current Workflow:** {step_info['flow_type'].replace('_', ' ').title()}
**Current Step:** {step_info['step'].replace('_', ' ').title()}
**Confidence:** {step_info['confidence']:.0%}
**Page Number:** {page_num + 1}
**Page Title:** {page_title}
**Page URL:** {page_url}

**Detected Keywords:** {', '.join(step_info['keywords_found'])}

**Employee Context:**
- Name: {profile.get('first_name')} {profile.get('last_name')}
- DOB: {profile.get('dob_date')}
- SSN: {profile.get('ssn', 'N/A')[-4:]} (last 4 digits)
- Hire Date: {profile.get('hire_date')}
- Tobacco Use: {profile.get('tobacco_use', 'Unknown')}

**Your Task:**
You are assisting with a benefits enrollment workflow. Based on the current step above,
fill in the form fields appropriately. Use the employee profile provided.

For enrollment workflows:
- Step 1-2: Personal info & group selection
- Step 3-4: Coverage type & plan selection
- Step 5-6: Beneficiary setup & health questions
- Step 7-8: Final review & confirmation

If you encounter fields for a DIFFERENT step than detected above, it's likely:
1. The page detection was incorrect (low confidence)
2. The form has unconventional layout
3. Multiple steps on one page

In those cases, fill what you can based on the field labels and current profile.
"""

    return prompt


def get_expected_fields_for_step(step: str) -> List[Dict]:
    """
    Return expected field names/patterns for each enrollment step.
    Helps LLM know what to look for.
    """
    expected = {
        "login": [
            {"name": "username", "type": "text", "example": "jdoe@company.com"},
            {"name": "password", "type": "password", "example": "***"},
        ],
        "employee_profile": [
            {"name": "first_name", "type": "text", "example": "John"},
            {"name": "last_name", "type": "text", "example": "Doe"},
            {"name": "dob", "type": "date", "example": "01/15/1990"},
            {"name": "ssn", "type": "text", "example": "123-45-6789"},
            {"name": "hire_date", "type": "date", "example": "01/15/2022"},
        ],
        "coverage_selection": [
            {"name": "coverage_type", "type": "select", "example": "Individual|Family|Spouse+Children"},
            {"name": "effective_date", "type": "date", "example": "05/01/2026"},
        ],
        "plan_selection": [
            {"name": "medical_plan", "type": "select", "example": "PPO|HMO|HDHP"},
            {"name": "dental_plan", "type": "select", "example": "Basic|Enhanced"},
            {"name": "vision_plan", "type": "select", "example": "Vision"},
        ],
        "beneficiary_setup": [
            {"name": "beneficiary_name", "type": "text", "example": "Jane Doe"},
            {"name": "beneficiary_relationship", "type": "select", "example": "Spouse|Child|Parent"},
            {"name": "beneficiary_dob", "type": "date", "example": "03/22/1992"},
        ],
        "health_questions": [
            {"name": "tobacco_use", "type": "select", "example": "Yes|No"},
            {"name": "disability_status", "type": "select", "example": "Yes|No"},
            {"name": "has_dependents", "type": "select", "example": "Yes|No"},
        ],
        "final_review": [
            {"name": "confirm_changes", "type": "checkbox", "example": "true"},
        ],
    }

    return expected.get(step, [])


def build_step_aware_fill_prompt(
    fields: List[Dict],
    profile: Dict,
    page: Page,
    page_num: int = 0,
) -> str:
    """
    Build a fill prompt that includes enrollment step context.
    """
    step_info = detect_enrollment_step(page, page_num)
    expected_fields = get_expected_fields_for_step(step_info["step"])

    workflow_context = get_workflow_context_prompt(page, profile, page_num)

    field_descriptors = []
    for field in fields[:25]:
        descriptor = {
            "id": field.get("id") or field.get("name") or "",
            "label": field.get("label") or field.get("placeholder") or "",
            "type": field.get("kind") or field.get("type") or "text",
            "visible": True,
        }
        options = field.get("options") or []
        if options:
            if isinstance(options[0], dict):
                descriptor["options"] = [option.get("text", "") for option in options[:10]]
            else:
                descriptor["options"] = [str(option) for option in options[:10]]
        field_descriptors.append(descriptor)

    prompt = workflow_context + f"""

### VISIBLE FORM FIELDS

Expected fields for this step:
{json.dumps(expected_fields, indent=2)}

Actual fields on page:
{json.dumps(field_descriptors, indent=2)}

### INSTRUCTIONS

Given the current enrollment step ({step_info['step']}) and the visible fields above,
map each field ID to the appropriate value from the employee profile.

Rules:
1. Dates must be MM/DD/YYYY format
2. For select/dropdown fields, use EXACT option text that matches available options
3. Only fill fields that match the current step (if page seems out of order, fill what makes sense)
4. Skip fields you cannot determine with certainty
5. Reply ONLY with valid JSON mapping field_id -> value

Employee Profile:
{json.dumps(profile, indent=2)}

JSON mapping (field_id -> value):
"""

    return prompt


def extract_enrollment_signals(page: Page) -> Dict:
    """
    Extract all signals about current enrollment state.
    Useful for debugging/logging what the system detected.
    """
    try:
        page_text = page.locator("body").evaluate("el => el.innerText")
    except Exception:
        page_text = ""

    step_info = detect_enrollment_step(page)

    return {
        **step_info,
        "page_title": page.title(),
        "page_url": page.url,
        "page_length": len(page_text),
        "has_login_fields": "password" in page.locator("body").evaluate("el => el.innerHTML").lower(),
        "has_form_fields": page.locator("input, select, textarea").count() > 0,
    }