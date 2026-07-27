from __future__ import annotations

"""Structured empirical memory for planning source-evolution experiments.

Unlike a retrieval-card blacklist, an observation binds a mechanism to its
actual source hook, activation evidence, verification state, and failure
signature.  It is deliberately compact enough to enter every Teacher packet.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.io import atomic_json, load_json, sha256_json
from ..core.models import CandidateResult, EvidenceVerdict


@dataclass(frozen=True)
class MechanismObservation:
    observation_id: str
    round_index: int
    hypothesis_id: str
    mechanism_family: str
    source_hooks: tuple[str, ...]
    retrieval_ids: tuple[str, ...]
    evidence_state: str
    epd_repairable: bool
    mechanism_fired: bool
    distance_gain: float
    metrics: dict[str, float]
    phase_signals: dict[str, float]
    failure_signature: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ObservationMemory:
    """Append-safe, source-scoped observation ledger used by the Teacher."""

    schema_version = "goalevolve.v2.observations.v1"

    def __init__(self, state_root: Path) -> None:
        self.path = state_root / "knowledge" / "observations.json"

    def records(self) -> list[dict[str, object]]:
        payload = load_json(self.path, {"observations": []}) or {"observations": []}
        return [dict(row) for row in list(payload.get("observations") or []) if isinstance(row, dict)]

    def record(self, *, round_index: int, candidate: CandidateResult, verdict: EvidenceVerdict) -> MechanismObservation:
        error = (candidate.evaluation_error or "").strip()
        failure = error or "; ".join(verdict.reasons)
        observation = MechanismObservation(
            observation_id=f"OBS_{sha256_json({'round': round_index, 'source': candidate.source_commit, 'hypothesis': candidate.hypothesis.hypothesis_id})[:16]}",
            round_index=round_index,
            hypothesis_id=candidate.hypothesis.hypothesis_id,
            mechanism_family=candidate.hypothesis.mechanism_family,
            source_hooks=tuple(candidate.hypothesis.source_hooks),
            retrieval_ids=tuple(candidate.hypothesis.retrieval_ids),
            evidence_state=verdict.state,
            epd_repairable=bool(error),
            mechanism_fired=verdict.mechanism_fired,
            distance_gain=float(verdict.distance_gain),
            metrics={str(key): float(value) for key, value in candidate.metrics.items()},
            phase_signals={str(key): float(value) for key, value in candidate.phase_signals.items()},
            failure_signature=failure[:1200],
        )
        rows = {str(row.get("observation_id") or ""): row for row in self.records()}
        rows[observation.observation_id] = observation.to_dict()
        atomic_json(self.path, {"schema_version": self.schema_version, "observations": sorted(rows.values(), key=lambda row: (int(row.get("round_index") or 0), str(row.get("observation_id") or "")))})
        return observation

    def summary(self, *, limit: int = 16) -> dict[str, object]:
        quarantine = load_json(self.path.parent / "evidence_quarantine.json", {}) or {}
        excluded = {str(item) for item in list(quarantine.get("hypothesis_ids") or [])}
        rows = [row for row in self.records() if str(row.get("hypothesis_id") or "") not in excluded]
        families: dict[str, dict[str, object]] = {}
        for row in rows:
            family = str(row.get("mechanism_family") or "unknown")
            hooks = tuple(row.get("source_hooks") or ())
            key = f"{family}:{'|'.join(hooks)}"
            aggregate = families.setdefault(
                key,
                {
                    "mechanism_family": family,
                    "source_hooks": list(hooks),
                    "attempts": 0,
                    "activation_count": 0,
                    "engineering_failures": 0,
                    "best_distance_gain": float("-inf"),
                    "last_state": "",
                    "last_failure_signature": "",
                },
            )
            aggregate["attempts"] = int(aggregate["attempts"]) + 1
            aggregate["activation_count"] = int(aggregate["activation_count"]) + int(bool(row.get("mechanism_fired")))
            aggregate["engineering_failures"] = int(aggregate["engineering_failures"]) + int(bool(row.get("epd_repairable")))
            aggregate["best_distance_gain"] = max(float(aggregate["best_distance_gain"]), float(row.get("distance_gain") or 0.0))
            aggregate["last_state"] = str(row.get("evidence_state") or "")
            aggregate["last_failure_signature"] = str(row.get("failure_signature") or "")[:400]
        compact = sorted(families.values(), key=lambda row: (int(row["activation_count"]), float(row["best_distance_gain"]), -int(row["engineering_failures"])), reverse=True)
        return {
            "schema_version": self.schema_version,
            "observation_count": len(rows),
            "family_hook_summary": compact[:limit],
            "recent_observations": rows[-limit:],
        }
