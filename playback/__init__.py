"""
playback
--------
Pure Playwright workflow execution package. No LLM dependencies.

Architecture:
  FakerValueGenerator — synthetic form-field value generation (Faker library)
  ElementLocator      — multi-strategy CSS/XPath element finding
  PlaybackSession     — full session lifecycle (browser, login, steps, Excel report)
  ExcelReporter       — colour-coded Excel run report
  run_execution_mode  — backward-compatible entry point

Public API::

    from playback import run_execution_mode
    from playback import PlaybackSession, PlaybackConfig, PlaybackResult
    from playback import FakerValueGenerator, ExcelReporter
"""
from .engine import run_execution_mode
from .session import PlaybackSession, ElementLocator
from .faker_values import FakerValueGenerator
from .excel_reporter import ExcelReporter, build_report_path
from .models import PlaybackConfig, PlaybackResult, WorkflowStep, StepType, StepResult

__all__ = [
    "run_execution_mode",
    "PlaybackSession",
    "PlaybackConfig",
    "PlaybackResult",
    "WorkflowStep",
    "StepType",
    "StepResult",
    "ElementLocator",
    "FakerValueGenerator",
    "ExcelReporter",
    "build_report_path",
]
