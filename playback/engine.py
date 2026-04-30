"""
playback.engine
---------------
Backward-compatible entry point for workflow playback.
Thin wrapper around PlaybackSession -- pure Playwright, no LLM.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from .models import PlaybackConfig
from .session import PlaybackSession

logger = logging.getLogger(__name__)


def run_execution_mode(
    workflow_data: Dict[str, Any],
    execution_profile: Dict[str, Any],
    headless: bool = False,
    speed_factor: float = 1.0,
    override_url: Optional[str] = None,
    update_callback: Optional[Callable] = None,
    group_name: Optional[str] = None,
    excel_report_path: str = "",
) -> Dict[str, Any]:
    """
    Execute a recorded workflow step-by-step using pure Playwright.

    Form fields are filled with Faker-generated synthetic values.
    Credential fields (username/password) use real values from config.properties.
    SELECT steps use the value captured during recording.

    Missing / failed steps are photographed (red overlay banner) and written
    to an Excel report at excel_report_path (or auto-generated under reports/).
    """
    start_url = override_url or workflow_data.get("start_url") or workflow_data.get("url", "")
    credentials: Dict[str, str] = {}
    try:
        from core.config_loader import load_credentials_for_url
        credentials = load_credentials_for_url(start_url) or {}
    except Exception as exc:
        logger.warning(f"Could not load credentials: {exc}")
    config = PlaybackConfig(
        workflow_data=workflow_data,
        execution_profile=execution_profile,
        credentials=credentials,
        start_url=start_url,
        headless=headless,
        group_name=group_name or "",
        update_callback=update_callback,
        speed_factor=speed_factor,
        excel_report_path=excel_report_path,
    )
    result = PlaybackSession(config).run()
    return result.to_dict()
