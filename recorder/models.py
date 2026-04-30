"""
recorder.models
---------------
Data models for the recording system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class StepType(Enum):
    INPUT = "input"
    SELECT = "select"
    CLICK = "click"
    CLICK_LINK = "click_link"
    TOGGLE = "toggle"


@dataclass
class RecordedStep:
    """A single user interaction captured during recording."""

    type: str
    page_id: str = ""
    page_url: str = ""
    page_title: str = ""
    tag: str = ""
    id: str = ""
    name: str = ""
    label: str = ""
    text: str = ""
    value: str = ""
    selector: str = ""
    input_type: str = ""
    placeholder: str = ""

    @classmethod
    def from_raw(cls, raw: Dict[str, Any]) -> "RecordedStep":
        """Build a RecordedStep from a raw JS event dict, coercing all values to str."""

        def _s(key: str) -> str:
            v = raw.get(key, "")
            return str(v) if v is not None else ""

        return cls(
            type=_s("type"),
            page_id=_s("page_id"),
            page_url=_s("page_url"),
            page_title=_s("page_title"),
            tag=_s("tag"),
            id=_s("id"),
            name=_s("name"),
            label=_s("label"),
            text=_s("text"),
            value=_s("value"),
            selector=_s("selector"),
            input_type=_s("input_type"),
            placeholder=_s("placeholder"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "page_id": self.page_id,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "tag": self.tag,
            "id": self.id,
            "name": self.name,
            "label": self.label,
            "text": self.text,
            "value": self.value,
            "selector": self.selector,
            "input_type": self.input_type,
            "placeholder": self.placeholder,
        }

    @property
    def step_type(self) -> Optional[StepType]:
        try:
            return StepType(self.type.lower())
        except (ValueError, AttributeError):
            return None

    @property
    def display_label(self) -> str:
        return self.label or self.text or self.name or self.id or f"({self.type})"


@dataclass
class RecordingResult:
    """Result of a completed recording session."""

    steps: List[RecordedStep] = field(default_factory=list)
    page_checkpoints: List[Dict[str, Any]] = field(default_factory=list)
    url: str = ""
    workflow_name: str = ""
    step_count: int = 0
    duration: float = 0.0
    captured_at: int = 0

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "url": self.url,
            "step_count": self.step_count,
            "duration": self.duration,
            "captured_at": self.captured_at,
            "page_checkpoint_count": len(self.page_checkpoints),
            "page_checkpoints": [dict(checkpoint) for checkpoint in self.page_checkpoints],
        }
