from __future__ import annotations

from typing import Sequence

from ..core.contracts import GoalContract
from ..core.models import CandidateResult, EvidenceVerdict, Parent


REQUIRED_CHECKS = ("build", "flow", "metrics", "lec")


def classify_candidate(*, contract: GoalContract, parent: Parent, candidate: CandidateResult, min_distance_gain: float = 1e-6) -> EvidenceVerdict:
    distance, _, missing = contract.evaluate(candidate.metrics)
    gain = parent.goal_distance - distance
    check_map = {item.name: item.passed for item in candidate.checks}
    missing_checks = [name for name in REQUIRED_CHECKS if not check_map.get(name, False)]
    # Telemetry values can be directional quantities (for example signed
    # leakage delta).  A negative nonzero delta proves that the mechanism ran;
    # it must not be treated as absent merely because positive counts are the
    # convention for other signals.  QoR improvement remains a separate,
    # mandatory promotion condition below.
    def _signal_observed(signal: str) -> bool:
        # Journal rollback telemetry is mandatory for audit, but zero is a
        # valid count.  Other mechanism signals continue to require a
        # nonzero value to show that the relevant action really occurred.
        if signal.endswith("_journal_rollbacks"):
            return signal in candidate.phase_signals
        return abs(float(candidate.phase_signals.get(signal, 0.0))) > 0.0

    activation_signals = (
        candidate.hypothesis.activation_signals
        or candidate.hypothesis.expected_signals
    )
    mechanism_fired = bool(candidate.phase_signals) and all(
        _signal_observed(signal) for signal in activation_signals
    )
    integrity_ok = not candidate.evaluation_error and bool(candidate.implementation_diff.strip()) and bool(candidate.source_commit.strip()) and not missing_checks
    hard_violations = contract.hard_violations(candidate.metrics)
    reasons: list[str] = []
    if candidate.legacy:
        reasons.append("legacy_evidence_cannot_promote_without_revalidation")
        state = "legacy"
    elif not integrity_ok:
        reasons.extend([f"failed_or_missing_check:{name}" for name in missing_checks])
        if not candidate.implementation_diff.strip():
            reasons.append("missing_implementation_diff")
        if not candidate.source_commit.strip():
            reasons.append("missing_source_commit")
        if candidate.evaluation_error:
            reasons.append(f"evaluation_error:{candidate.evaluation_error}")
        state = "invalid"
    elif hard_violations:
        reasons.extend(hard_violations)
        state = "refuted"
    elif gain <= min_distance_gain:
        reasons.append(f"goal_distance_not_improved:{gain:.6g}")
        state = "refuted"
    elif not mechanism_fired:
        # A missing telemetry line weakens causal attribution, but cannot
        # invalidate a reproducibly measured candidate that passed the full
        # build -> post-route -> metrics -> official 4/4 LEC contract.  Keep
        # the uncertainty explicit so later rounds can add or repair the
        # instrumentation, while allowing verified QoR to remain on the
        # evolution lineage.
        reasons.append("verified_qor_gain_with_missing_mechanism_telemetry")
        state = "verified_qor_unattributed"
    else:
        reasons.append("four_of_four_checks_passed_and_mechanism_fired")
        state = "validated"
    if missing:
        reasons.extend(f"missing_metric:{name}" for name in missing)
        if state == "validated":
            state = "invalid"
    return EvidenceVerdict(state, distance, gain, mechanism_fired, integrity_ok, tuple(reasons))
