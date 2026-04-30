"""
recorder
--------
OOP browser interaction recorder.

Records ONLY what the user actually clicks and types -- no full-page scanning.

Public API::

    from recorder import Recorder, RecordingSession, RecordedStep, RecordingResult, StepType
    from recorder import start_teaching_mode, get_last_recording_metadata, stop_recording
"""
from __future__ import annotations
import logging
from typing import Any, Callable, Dict, List, Optional

from .models import RecordedStep, RecordingResult, StepType
from .recorder import Recorder
from .session import RecordingSession

from core.constants import stop_recording_event

logger = logging.getLogger(__name__)

_recorder_instance: Any = None
_last_metadata: Dict[str, Any] = {}


def start_teaching_mode(
    url: str,
    workflow_name: str = "workflow.json",
    headless: bool = False,
    update_callback: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    """Start recording browser interactions. Returns normalised step dicts."""
    global _recorder_instance, _last_metadata
    _recorder_instance = Recorder(stop_event=stop_recording_event)
    steps = _recorder_instance.record(
        url=url,
        workflow_name=workflow_name,
        headless=headless,
        update_callback=update_callback,
    )
    _last_metadata = _recorder_instance.last_metadata
    return steps


def get_last_recording_metadata() -> Dict[str, Any]:
    """Return metadata from the most recent recording."""
    if _recorder_instance is not None:
        return _recorder_instance.last_metadata
    return _last_metadata


def stop_recording() -> None:
    """Signal the recording loop to stop."""
    stop_recording_event.set()


__all__ = [
    "Recorder",
    "RecordingSession",
    "RecordedStep",
    "RecordingResult",
    "StepType",
    "start_teaching_mode",
    "get_last_recording_metadata",
    "stop_recording",
]