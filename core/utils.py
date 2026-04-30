"""
shared.utils
~~~~~~~~~~~
Pure text / string utilities shared across every other module.
No Selenium dependency; no side effects.
"""

import json


def normalize_text(value: object) -> str:
    """Return a lowercase alphanumeric-only fingerprint of *value*."""
    return "".join(ch.lower() for ch in str(value) if ch.isalnum())


def clean_text(value: object) -> str:
    """Collapse whitespace and strip *value* to a clean string."""
    return " ".join(str(value or "").split()).strip()


def safe_lower(value: object) -> str:
    """``clean_text`` followed by ``str.lower``."""
    return clean_text(value).lower()


def candidate_values(*values: object) -> list[str]:
    """Return a de-duplicated list of non-empty cleaned values."""
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        cleaned = clean_text(v)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def css_attr_selector(attr: str, value: str, tag: str = "") -> str:
    """Build a CSS attribute selector fragment, e.g. ``input[name="foo"]``."""
    cleaned = clean_text(value)
    if not cleaned:
        return ""
    return f"{tag}[{attr}={json.dumps(cleaned)}]"


def is_placeholder_select_option(text: str, value: str) -> bool:
    """Return *True* when an ``<option>`` represents a placeholder / empty choice."""
    text_norm = safe_lower(text)
    value_norm = normalize_text(value)
    return (
        not text_norm
        or text_norm
        in {"select", "select one", "choose", "please select", "-- select --", "n/a", "na"}
        or "select" in text_norm
        or "choose" in text_norm
        or "please" in text_norm
        or value_norm in {"", "0", "-1", "none", "null"}
    )


# ---------------------------------------------------------------------------
# Private aliases kept for internal backward-compat (used heavily in other
# modules via  ``from core.utils import _normalize_text`` style).
# ---------------------------------------------------------------------------
_normalize_text = normalize_text
_clean_text = clean_text
_safe_lower = safe_lower
_candidate_values = candidate_values
_css_attr_selector = css_attr_selector
_is_placeholder_select_option = is_placeholder_select_option
