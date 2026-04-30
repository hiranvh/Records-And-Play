"""
recorder.recorder
-----------------
Public entry point for the Recorder class.

Usage::

    import threading
    from recorder import Recorder

    stop = threading.Event()
    r = Recorder(stop_event=stop)

    # In the main thread (blocks until stop() is called):
    steps = r.record("https://example.com", workflow_name="my_flow.json")

    # From another thread to stop the recording:
    r.stop()

    # Metadata about the last recording:
    meta = r.last_metadata
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional

from .models import RecordingResult
from .session import RecordingSession


class Recorder:
    """
    High-level recorder that manages a RecordingSession lifecycle.

    Instantiate once, call record() to start a blocking recording session,
    call stop() from another thread to end it.
    """

    def __init__(self, stop_event: Optional[threading.Event] = None) -> None:
        self._stop_event: threading.Event = stop_event or threading.Event()
        self._last_result: Optional[RecordingResult] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        url: str,
        workflow_name: str = "workflow.json",
        headless: bool = False,
        update_callback: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Start a recording session and block until stop() is called.

        Args:
            url:           URL to navigate to before recording begins.
            workflow_name: Identifier stored in the recording metadata.
            headless:      Whether to run the browser in headless mode.

        Returns:
            List of step dicts compatible with the workflow JSON schema.
        """
        session = RecordingSession(
            url=url,
            workflow_name=workflow_name,
            headless=headless,
            stop_event=self._stop_event,
            update_callback=update_callback,
        )
        self._last_result = session.start()
        return [s.to_dict() for s in self._last_result.steps]

    def stop(self) -> None:
        """Signal the active recording session to stop."""
        self._stop_event.set()

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def last_metadata(self) -> Dict[str, Any]:
        """Metadata dict from the most recent recording session."""
        if self._last_result:
            return self._last_result.to_metadata()
        return {}

    @property
    def last_result(self) -> Optional[RecordingResult]:
        """Full RecordingResult from the most recent session."""
        return self._last_result
