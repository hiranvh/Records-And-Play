"""
playback.report_service
-----------------------
Excel report helpers for playback sessions.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Tuple

from .models import PlaybackConfig, StepResult, WorkflowStep


class ReportService:
    """Pure helpers for replay report generation."""

    @staticmethod
    def write_excel(
        cfg: PlaybackConfig,
        pairs: List[Tuple[WorkflowStep, StepResult]],
        duration_seconds: float,
        log: Callable[[str, str], None],
    ) -> str:
        """Build the Excel report from accumulated step/result pairs."""
        try:
            from .excel_reporter import ExcelReporter, build_report_path

            report_path = cfg.excel_report_path or build_report_path(
                cfg.workflow_data.get("name", "playback")
            )

            rows = []
            for step, sr in pairs:
                if sr.skipped:
                    status = "SKIP"
                elif sr.success:
                    status = "PASS"
                else:
                    status = "FAIL"

                rows.append({
                    "step_index":  step.index + 1,
                    "label":       sr.step_label,
                    "step_type":   step.type,
                    "faker_value": sr.faker_value,
                    "status":      status,
                    "reason":      "" if sr.success else sr.message,
                    "screenshot":  sr.screenshot_path or "",
                    "timestamp":   datetime.now().isoformat(timespec="seconds"),
                })

            saved = ExcelReporter(report_path).write(
                rows,
                workflow_name=cfg.workflow_data.get("name", ""),
                duration_seconds=duration_seconds,
            )
            log(f"Excel report: {saved}")
            return saved
        except Exception as exc:
            log(f"Excel report error: {exc}", "WARNING")
            return ""
