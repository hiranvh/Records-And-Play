"""
shared/__init__.py
------------------
Shared application-wide constants and pure utilities.
"""
from .constants import stop_recording_event, stop_execution_event  # noqa: F401
from .config_loader import load_credentials_for_url  # noqa: F401
from .profile import generate_profile, identify_profile_field, get_profile_value  # noqa: F401
from .utils import normalize_text, clean_text, safe_lower  # noqa: F401

__all__ = [
    "stop_recording_event",
    "stop_execution_event",
    "load_credentials_for_url",
    "generate_profile",
    "identify_profile_field",
    "get_profile_value",
    "normalize_text",
    "clean_text",
    "safe_lower",
]