"""
core.workflow
-------------
Shared workflow JSON I/O and step normalization.

Public API::

    from core.workflow import save_workflow, load_workflow, compact_workflow
    from core.workflow import rename_workflow, validate_workflow, get_workflow_path
    from core.workflow import normalize_step, normalize_workflow_steps
"""
from .workflow_io import (
    save_workflow, load_workflow, compact_workflow,
    rename_workflow, validate_workflow, get_workflow_path,
)
from .normalizer import normalize_step, normalize_workflow_steps, deduplicate_steps

__all__ = [
    "save_workflow", "load_workflow", "compact_workflow",
    "rename_workflow", "validate_workflow", "get_workflow_path",
    "normalize_step", "normalize_workflow_steps", "deduplicate_steps",
]
