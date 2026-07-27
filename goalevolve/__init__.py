"""GoalEvolve v2: pluggable execution with frozen-goal evidence control."""

from .core.contracts import GoalContract, MetricSpec, build_contract
from .execution.engine import GoalEvolveEngine

__all__ = ["GoalContract", "MetricSpec", "GoalEvolveEngine", "build_contract"]
