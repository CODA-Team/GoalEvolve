"""Advisory search policy derived from committed evidence only.

This module deliberately cannot nominate a parent, create a Student role, or
alter promotion.  It gives the Teacher a compact, auditable account of which
existing evidence and source facts deserve another look.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from goalevolve.core.io import atomic_json, load_json
from goalevolve.core.models import Parent
from goalevolve.planning.repository_graph import RepositoryGraph


SEARCH_POLICY_SCHEMA_VERSION = "goalevolve.search_policy.v1"
_DIVERSIFICATION_STREAK = 2


class SearchPolicyBuilder:
    """Build policy facts without exercising any execution authority."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)

    def build(
        self,
        *,
        parent: Parent,
        diagnosis: object,
        epd_portfolio: Mapping[str, object],
        repository_graph: RepositoryGraph,
        allowed_patch_roots: Sequence[str] = (),
    ) -> dict[str, object]:
        nonpromoted_rounds = self._recent_nonpromoted_rounds()
        streak = len(nonpromoted_rounds)
        bottleneck = str(getattr(diagnosis, "dominant_bottleneck", "") or "")
        diagnosis_payload = diagnosis.to_dict() if hasattr(diagnosis, "to_dict") else {}
        validated = [
            dict(record)
            for record in list(epd_portfolio.get("records") or ())
            if isinstance(record, Mapping) and str(record.get("epd_status") or "") == "validated"
        ]
        validated.sort(
            key=lambda record: (
                -float(record.get("elite_score") or 0.0),
                -float(record.get("distance_gain") or 0.0),
                str(record.get("record_id") or ""),
            )
        )
        source_hooks = tuple(
            str(path)
            for record in validated
            for path in list(record.get("source_hooks") or ())
            if str(path)
        )
        metric_hints = _metric_hints(bottleneck, diagnosis_payload)
        graph_packet = repository_graph.focus(
            anchor_hints=source_hooks,
            metric_hints=metric_hints,
            allowed_patch_roots=allowed_patch_roots,
        )
        diversification_required = streak >= _DIVERSIFICATION_STREAK
        stagnation = self._stagnation_frontier(nonpromoted_rounds)
        repeated_hooks = [
            str(row["source_hook"])
            for row in list(stagnation["repeated_hook_frontier"])
            if int(row["attempt_count"]) >= _DIVERSIFICATION_STREAK
        ]
        return {
            "schema_version": SEARCH_POLICY_SCHEMA_VERSION,
            "advisory_only": True,
            "promotion_authority": False,
            "parent": parent.to_dict(),
            "hill_climb": {
                "incumbent_parent_id": parent.parent_id,
                "incumbent_source_hash": parent.source_hash,
                "alternative_parent_ids": [],
                "acceptance_authority": "existing_promotion_policy_only",
            },
            "diagnosis": diagnosis_payload,
            "no_promotion_streak": streak,
            "stagnation": stagnation,
            "diversification": {
                "required": diversification_required,
                "threshold_completed_rounds": _DIVERSIFICATION_STREAK,
                "constraints": (
                    [
                        "Use a materially different source hook or decision boundary than the most recent unpromoted attempts.",
                        "Keep the Controller-supplied Student roles and all source/evaluation guards unchanged.",
                    ]
                    if diversification_required
                    else []
                ),
                "avoid_exact_source_hooks": repeated_hooks if diversification_required else [],
            },
            "elite_record_ids": [str(record.get("record_id") or "") for record in validated if str(record.get("record_id") or "")],
            "elite_source_hooks": list(dict.fromkeys(source_hooks)),
            "repository_graph": {
                "source_hash": repository_graph.source_hash,
                "base_source_hash": repository_graph.base_source_hash,
                "artifact_root": str(repository_graph.artifact_root),
                "focus": graph_packet,
            },
        }

    @staticmethod
    def persist(*, round_root: Path, policy: Mapping[str, object]) -> Path:
        path = Path(round_root) / "search_policy.json"
        atomic_json(path, dict(policy))
        return path

    def _no_promotion_streak(self) -> int:
        return len(self._recent_nonpromoted_rounds())

    def _recent_nonpromoted_rounds(self) -> list[dict[str, Any]]:
        rounds_root = self.state_root / "rounds"
        if not rounds_root.is_dir():
            return []
        summaries: list[dict[str, Any]] = []
        for path in rounds_root.glob("round_*/round.json"):
            payload = load_json(path, {})
            if isinstance(payload, dict) and isinstance(payload.get("round"), int):
                summaries.append(payload)
        retained: list[dict[str, Any]] = []
        for summary in sorted(summaries, key=lambda item: int(item["round"]), reverse=True):
            if summary.get("promoted_student"):
                break
            retained.append(summary)
        return retained

    @staticmethod
    def _stagnation_frontier(rounds: Sequence[Mapping[str, Any]]) -> dict[str, object]:
        attempts: list[dict[str, object]] = []
        hooks: dict[str, dict[str, object]] = {}
        for summary in sorted(rounds, key=lambda item: int(item.get("round") or 0)):
            hypotheses = {
                str(item.get("hypothesis_id") or ""): dict(item)
                for item in list(dict(summary.get("teacher_plan") or {}).get("hypotheses") or ())
                if isinstance(item, Mapping) and str(item.get("hypothesis_id") or "")
            }
            for result in list(summary.get("results") or ()):
                if not isinstance(result, Mapping):
                    continue
                hypothesis = hypotheses.get(str(result.get("hypothesis_id") or ""), {})
                verdict = dict(result.get("verdict") or {})
                source_hooks = tuple(
                    str(path)
                    for path in list(hypothesis.get("source_hooks") or ())
                    if str(path)
                )
                row = {
                    "round": int(summary.get("round") or 0),
                    "student_id": str(result.get("student_id") or ""),
                    "hypothesis_id": str(result.get("hypothesis_id") or ""),
                    "source_hooks": list(source_hooks),
                    "evidence_state": str(verdict.get("state") or ""),
                    "distance_gain": float(verdict.get("distance_gain") or 0.0),
                    "mechanism_fired": bool(verdict.get("mechanism_fired")),
                    "reasons": [str(reason) for reason in list(verdict.get("reasons") or ()) if str(reason)],
                }
                attempts.append(row)
                for hook in source_hooks:
                    aggregate = hooks.setdefault(
                        hook,
                        {
                            "source_hook": hook,
                            "attempt_count": 0,
                            "activation_count": 0,
                            "best_distance_gain": float("-inf"),
                            "last_evidence_state": "",
                        },
                    )
                    aggregate["attempt_count"] = int(aggregate["attempt_count"]) + 1
                    aggregate["activation_count"] = int(aggregate["activation_count"]) + int(row["mechanism_fired"])
                    aggregate["best_distance_gain"] = max(
                        float(aggregate["best_distance_gain"]),
                        float(row["distance_gain"]),
                    )
                    aggregate["last_evidence_state"] = str(row["evidence_state"])
        frontier = sorted(
            hooks.values(),
            key=lambda row: (
                -int(row["attempt_count"]),
                -int(row["activation_count"]),
                str(row["source_hook"]),
            ),
        )
        return {
            "recent_nonpromoted_attempts": attempts,
            "repeated_hook_frontier": frontier,
        }


def _metric_hints(bottleneck: str, diagnosis: Mapping[str, object]) -> tuple[str, ...]:
    tokens = [bottleneck]
    tokens.extend(str(item) for item in list(diagnosis.get("active_metrics") or ()) if str(item))
    joined = " ".join(tokens).lower()
    if any(token in joined for token in ("tns", "wns", "timing")):
        tokens.extend(("timing", "tns", "slack"))
    if any(token in joined for token in ("power", "leakage", "dynamic")):
        tokens.extend(("power", "leakage", "dynamic"))
    return tuple(dict.fromkeys(token for token in tokens if token))
