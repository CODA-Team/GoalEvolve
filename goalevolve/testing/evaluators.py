from __future__ import annotations

from ..core.models import CandidateResult, CheckResult, Hypothesis, Parent


class MockEvaluator:
    """Deterministic evaluator for controller/feedback-loop smoke tests only."""

    name = "mock"

    def evaluate(self, *, contract, parent: Parent, hypothesis: Hypothesis, student_id: str, workspace: Path, round_index: int) -> CandidateResult:
        metrics = dict(parent.metrics)
        index = int(student_id.rsplit("_", 1)[-1]) if student_id.rsplit("_", 1)[-1].isdigit() else 0
        active = next((spec for spec in contract.metrics if spec.residual(metrics.get(spec.name)) and spec.residual(metrics.get(spec.name)) > 0), contract.metrics[0])
        # At least one student is a genuine attributed improvement every round;
        # other students exercise refutation/local-only gates.
        if index == 1:
            factor = 0.74 if active.minimize else 1.26
            metrics[active.name] = metrics[active.name] * factor
            signals = {name: 1.0 for name in (*hypothesis.expected_signals, *hypothesis.activation_signals)}
        elif index == 2:
            factor = 0.82 if active.minimize else 1.18
            metrics[active.name] = metrics[active.name] * factor
            signals = {}
        elif index == 3:
            factor = 1.08 if active.minimize else 0.92
            metrics[active.name] = metrics[active.name] * factor
            signals = {name: 1.0 for name in (*hypothesis.expected_signals, *hypothesis.activation_signals)}
        else:
            signals = {name: 1.0 for name in (*hypothesis.expected_signals, *hypothesis.activation_signals)}
        checks = [CheckResult(name, True, "mock-pass") for name in ("build", "flow", "metrics", "lec")]
        diff = f"--- a/{hypothesis.source_hooks[0]}\n+++ b/{hypothesis.source_hooks[0]}\n+// mock round {round_index} {hypothesis.hypothesis_id}\n"
        return CandidateResult(student_id, hypothesis, metrics, signals, checks, diff, f"mock-{round_index}-{student_id}")
