"""Workflow package — daily execution loops."""

import importlib.util
import sys
from pathlib import Path

# Load DailyWorkflow from the parent module hermes_trader.workflow (workflow.py)
# without triggering a circular import with the package.
_workflow_py = Path(__file__).parent.parent / "workflow.py"
spec = importlib.util.spec_from_file_location("hermes_trader._workflow_mod", _workflow_py)
assert spec is not None and spec.loader is not None
_workflow_mod = importlib.util.module_from_spec(spec)
sys.modules["hermes_trader._workflow_mod"] = _workflow_mod
spec.loader.exec_module(_workflow_mod)

DailyWorkflow = _workflow_mod.DailyWorkflow  # type: ignore

# EnhancedDailyWorkflow archived — workflow.py is the single production engine.
# from hermes_trader._archive.enhanced_daily_workflow import EnhancedDailyWorkflow

__all__ = ["DailyWorkflow"]