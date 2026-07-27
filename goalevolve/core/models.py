from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    mechanism_family: str
    claim: str
    source_hooks: tuple[str, ...]
    expected_signals: tuple[str, ...]
    retrieval_ids: tuple[str, ...]
    novelty_key: str
    scope_evidence: tuple[str, ...] = ()
    # An optional exact source boundary imposed by the active scheduler.  It
    # is stricter than the campaign-wide allowed roots and is enforced before
    # an expensive private build can start.
    allowed_patch_paths: tuple[str, ...] = ()
    # Selects the official Tcl optimization sequence for this candidate.  It
    # is scheduler-owned, never a free-form Student decision.
    evaluation_mode: str = "timing_only"
    # A named, bounded repair_timing recipe.  It is part of the evaluation
    # identity, so results from distinct schedules never share a baseline.
    timing_recipe_id: str = "legacy_setup"
    # A mechanism can emit outcome telemetry whose honest value is zero (for
    # example, a refreshed choice that happened to match the stale choice).
    # These signals are still collected for diagnosis, but only this subset
    # proves that the source-side mechanism boundary actually executed.  An
    # empty tuple preserves the historical rule that every expected signal is
    # an activation requirement.
    activation_signals: tuple[str, ...] = ()
    # Some mechanisms expose an explicit, source-owned log record proving
    # that their admission condition was reached but no eligible action
    # existed.  This is conclusive negative evidence, not missing telemetry:
    # repeating the same card on an unchanged parent cannot repair it.
    conclusive_nonactivation_patterns: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CandidateResult:
    student_id: str
    hypothesis: Hypothesis
    metrics: dict[str, float]
    phase_signals: dict[str, float]
    checks: list[CheckResult]
    implementation_diff: str
    source_commit: str
    artifacts: dict[str, str] = field(default_factory=dict)
    legacy: bool = False
    evaluation_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "student_id": self.student_id,
            "hypothesis": self.hypothesis.to_dict(),
            "metrics": self.metrics,
            "phase_signals": self.phase_signals,
            "checks": [asdict(item) for item in self.checks],
            "implementation_diff": self.implementation_diff,
            "source_commit": self.source_commit,
            "artifacts": self.artifacts,
            "legacy": self.legacy,
            "evaluation_error": self.evaluation_error,
        }


@dataclass(frozen=True)
class EvidenceVerdict:
    state: str
    goal_distance: float
    distance_gain: float
    mechanism_fired: bool
    integrity_ok: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Parent:
    parent_id: str
    metrics: dict[str, float]
    source_commit: str
    source_hash: str
    goal_distance: float
    # QoR is comparable only when parent and candidates ran the same official
    # Tcl optimization sequence.  A power-first parent is initially measured
    # with ``power_only`` and must be remeasured before timing recovery.
    evaluation_mode: str = "unknown"
    # A post-route operating point is the product of source *and* controller
    # schedule.  Persist the recipe that produced it so an executable QoR
    # champion does not silently fall back to a different Tcl intervention in
    # the next round.  Older state files default to the historical schedule.
    timing_recipe_id: str = "legacy_setup"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
