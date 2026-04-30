"""
shared.constants
~~~~~~~~~~~~~~~
All application-wide constants and configuration values.
"""

import threading

# ---------------------------------------------------------------------------
# Threading events exposed to the outside world (e.g., GUI stop buttons)
# ---------------------------------------------------------------------------
stop_recording_event: threading.Event = threading.Event()
stop_execution_event: threading.Event = threading.Event()

# ---------------------------------------------------------------------------
# Workflow schema
# ---------------------------------------------------------------------------
WORKFLOW_SCHEMA_VERSION: int = 2
DEFAULT_VALID_ZIP: str = "20705"

# ---------------------------------------------------------------------------
# Self-healing correction memory
# ---------------------------------------------------------------------------
CORRECTION_MEMORY_FILENAME: str = "self_heal_memory.json"
CORRECTION_MEMORY_HALF_LIFE_DAYS: float = 21.0
CORRECTION_MEMORY_MIN_SCORE: float = 0.12
CORRECTION_MEMORY_PRUNE_MAX_AGE_DAYS: float = 120.0
CORRECTION_MEMORY_PRUNE_MIN_SCORE: float = 0.05
CORRECTION_MEMORY_MAX_ENTRIES: int = 800

# ---------------------------------------------------------------------------
# Navigation instruction memory (user guidance replay)
# ---------------------------------------------------------------------------
NAV_INSTRUCTION_MEMORY_FILENAME: str = "nav_instruction_memory.json"

# ---------------------------------------------------------------------------
# Page field memory (per-URL field value cache, learns from each run)
# ---------------------------------------------------------------------------
PAGE_FIELD_MEMORY_FILENAME: str = "page_field_memory.json"

# ---------------------------------------------------------------------------
# Utility element blacklists — elements we must NEVER click or fill during
# automated form traversal
# ---------------------------------------------------------------------------
UTILITY_INPUT_PATTERNS: tuple[str, ...] = (
    "faqsearch",
    "globalsearch",
    "globalmembersearch",
    "membersearch",
    "recordsperpage",
    "search",
    "contactus",
    "contact",
    "notification",
    "notifications",
    "effectivedate",
)

UTILITY_CLICK_PATTERNS: tuple[str, ...] = (
    "faq",
    "faqsearch",
    "help",
    "support",
    "knowledgebase",
    "documentation",
    "globalsearch",
    "contactus",
    "contact",
    "notification",
    "notifications",
)

# ---------------------------------------------------------------------------
# ZIP → location lookup table (expandable)
# ---------------------------------------------------------------------------
ZIP_LOCATION_DATA: dict[str, dict[str, str]] = {
    "20705": {
        "county": "PRINCE GEORGES",
        "city": "BELTSVILLE",
        "state": "Maryland",
        "billing_location": "",
        "employee_class": "",
    },
}

# ---------------------------------------------------------------------------
# LLM model path (resolved at runtime relative to this package)
# ---------------------------------------------------------------------------
import os

LLM_MODEL_FILENAME: str = "phi-3.5-mini-instruct-q4_k_m.gguf"
LLM_MODEL_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models",
)
LLM_MODEL_PATH: str = os.path.join(LLM_MODEL_DIR, LLM_MODEL_FILENAME)
