"""Execution gold stays distinct from reply support/explanation labels."""

from typing import Literal

from app.schemas.judging import HumanLabel


class ExecutionGoldLabel(HumanLabel):
    execution_problem: Literal["problem", "clear", "insufficient"]
