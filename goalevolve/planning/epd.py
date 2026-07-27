from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from ..core.io import atomic_json, load_json, sha256_json
from ..core.models import CandidateResult, EvidenceVerdict, Parent


EPD_STATUSES = ("validated", "promising", "pending", "invalid")


@dataclass(frozen=True)
class EPDRecord:
    record_id: str
    parent_id: str
    source_hash: str
    hypothesis_id: str
    mechanism_family: str
    epd_status: str
    evidence_state: str
    metrics: dict[str, float]
    goal_distance: float | None
    distance_gain: float | None
    checkpoint_metrics: dict[str, dict[str, float]]
    artifacts: dict[str, str]
    round_index: int
    updated_at: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def epd_status(verdict: EvidenceVerdict, candidate: CandidateResult) -> str:
    """Map execution/evidence outcomes to the four paper-level EPD states.

    ``promising`` retains a valid QoR gain that lacks causal attribution;
    ``pending`` retains an implementable experiment whose full-flow evidence is
    incomplete, enabling bounded engineering repair instead of a false reject.
    """
    if verdict.state == "validated":
        return "validated"
    if verdict.state in {"local_only", "verified_qor_unattributed"} and verdict.distance_gain > 0:
        return "promising"
    if candidate.evaluation_error and "codex_failed" not in candidate.evaluation_error:
        return "pending"
    return "invalid"


class EvolutionProgramDatabase:
    """Persistent program/mechanism evidence store used by Teacher diagnosis.

    The file is a materialized, append-safe campaign record rather than a cache:
    every evaluated child obtains one immutable record keyed by source hash.
    """

    def __init__(self, state_root: Path) -> None:
        self.path = state_root / "knowledge" / "epd.json"

    def records(self) -> list[dict[str, object]]:
        payload = load_json(self.path, {"records": []}) or {"records": []}
        return [dict(item) for item in list(payload.get("records") or []) if isinstance(item, dict)]

    def record(
        self,
        *,
        round_index: int,
        parent: Parent,
        candidate: CandidateResult,
        verdict: EvidenceVerdict,
    ) -> EPDRecord:
        checkpoints = self._checkpoint_metrics(candidate.artifacts.get("checkpoint_metrics"))
        record = EPDRecord(
            record_id=f"EPD_{sha256_json({'source': candidate.source_commit, 'hypothesis': candidate.hypothesis.hypothesis_id})[:16]}",
            parent_id=parent.parent_id,
            source_hash=candidate.source_commit,
            hypothesis_id=candidate.hypothesis.hypothesis_id,
            mechanism_family=candidate.hypothesis.mechanism_family,
            epd_status=epd_status(verdict, candidate),
            evidence_state=verdict.state,
            metrics=dict(candidate.metrics),
            goal_distance=None if verdict.goal_distance == float("inf") else verdict.goal_distance,
            distance_gain=verdict.distance_gain,
            checkpoint_metrics=checkpoints,
            artifacts=dict(candidate.artifacts),
            round_index=round_index,
            updated_at=int(time.time()),
        )
        existing = {str(item.get("record_id") or ""): item for item in self.records()}
        existing[record.record_id] = record.to_dict()
        atomic_json(self.path, {"schema_version": "goalevolve.v2.epd.v1", "statuses": list(EPD_STATUSES), "records": sorted(existing.values(), key=lambda item: (int(item.get("round_index") or 0), str(item.get("record_id") or "")))})
        return record

    def ensure_baseline(self, parent: Parent) -> None:
        """Register the configured baseline explicitly until a full baseline flow is attached."""
        record_id = f"EPD_baseline_{parent.source_hash[:16]}"
        if any(str(row.get("record_id") or "") == record_id for row in self.records()):
            return
        record = EPDRecord(
            record_id=record_id,
            parent_id=parent.parent_id,
            source_hash=parent.source_hash,
            hypothesis_id="baseline",
            mechanism_family="baseline",
            epd_status="pending",
            evidence_state="baseline_configured_metrics",
            metrics=dict(parent.metrics),
            goal_distance=parent.goal_distance,
            distance_gain=None,
            checkpoint_metrics={},
            artifacts={},
            round_index=0,
            updated_at=int(time.time()),
        )
        rows = self.records()
        rows.append(record.to_dict())
        atomic_json(self.path, {"schema_version": "goalevolve.v2.epd.v1", "statuses": list(EPD_STATUSES), "records": rows})

    def attach_baseline_evaluation(self, *, parent: Parent, metrics: dict[str, float], goal_distance: float, artifacts: dict[str, str], passed: bool) -> None:
        """Replace configured p0 metadata with an actual same-flow baseline run."""
        record_id = f"EPD_baseline_{parent.source_hash[:16]}"
        record = EPDRecord(
            record_id=record_id,
            parent_id=parent.parent_id,
            source_hash=parent.source_hash,
            hypothesis_id="baseline",
            mechanism_family="baseline",
            epd_status="validated" if passed else "pending",
            evidence_state="baseline_measured_4of4" if passed else "baseline_measurement_incomplete",
            metrics=dict(metrics),
            goal_distance=goal_distance,
            distance_gain=None,
            checkpoint_metrics=self._checkpoint_metrics(artifacts.get("checkpoint_metrics")),
            artifacts=dict(artifacts),
            round_index=0,
            updated_at=int(time.time()),
        )
        rows = [row for row in self.records() if str(row.get("record_id") or "") != record_id]
        rows.append(record.to_dict())
        atomic_json(self.path, {"schema_version": "goalevolve.v2.epd.v1", "statuses": list(EPD_STATUSES), "records": rows})

    def summary(self, *, limit: int = 12) -> dict[str, object]:
        rows = self.records()
        strategy_rows = [
            row for row in rows
            if str(row.get("mechanism_family") or "") != "baseline"
        ]
        counts = {status: 0 for status in EPD_STATUSES}
        for row in strategy_rows:
            status = str(row.get("epd_status") or "invalid")
            if status in counts:
                counts[status] += 1
        ordered = sorted(strategy_rows, key=lambda row: (float(row.get("goal_distance") if row.get("goal_distance") is not None else float("inf")), -float(row.get("distance_gain") or 0.0)))
        return {
            "status_counts": counts,
            "record_count": len(strategy_rows),
            "baseline_record_count": len(rows) - len(strategy_rows),
            "total_record_count": len(rows),
            "recent_records": ordered[:limit],
        }

    def teacher_summary(self, *, limit: int = 16) -> dict[str, object]:
        """Return decision-bearing EPD evidence without replaying raw artifacts.

        The full EPD remains the campaign's audit record.  Giving it verbatim
        to a persistent Teacher, however, grows the prompt with checkpoint
        matrices and artifact maps from every historical candidate.  That
        makes old logs compete with the active parent diagnosis.  This view
        retains the evidence needed for retain/refine/suppress decisions and
        leaves stable artifact paths for on-demand inspection.
        """
        quarantine = load_json(self.path.parent / "evidence_quarantine.json", {}) or {}
        excluded = {str(item) for item in list(quarantine.get("hypothesis_ids") or [])}
        all_rows = [row for row in self.records() if str(row.get("hypothesis_id") or "") not in excluded]
        # Baseline nodes are experiment-boundary provenance, not strategies.
        # Counting configured parent placeholders as ``pending`` made the
        # Teacher and terminal reports claim there were unevaluated mechanisms
        # when none existed.  Parent metrics/checkpoints are already supplied
        # through the Parent and diagnosis packets; keep baselines only in the
        # full audit artifact.
        rows = [
            row for row in all_rows
            if str(row.get("mechanism_family") or "") != "baseline"
        ]
        counts = {status: 0 for status in EPD_STATUSES}
        for row in rows:
            status = str(row.get("epd_status") or "invalid")
            if status in counts:
                counts[status] += 1

        def compact(row: dict[str, object]) -> dict[str, object]:
            artifacts = dict(row.get("artifacts") or {})
            return {
                "record_id": row.get("record_id"),
                "round_index": row.get("round_index"),
                "hypothesis_id": row.get("hypothesis_id"),
                "mechanism_family": row.get("mechanism_family"),
                "epd_status": row.get("epd_status"),
                "evidence_state": row.get("evidence_state"),
                "metrics": row.get("metrics"),
                "goal_distance": row.get("goal_distance"),
                "distance_gain": row.get("distance_gain"),
                "checkpoint_metrics_artifact": artifacts.get("checkpoint_metrics"),
                "official_4of4_artifact": artifacts.get("official_4of4_log"),
            }

        # Preserve both the best known mechanisms and the most recent failed
        # attempts: the former establishes integration anchors, the latter
        # prevents accidental repetition.  Record IDs de-duplicate overlap.
        ranked = sorted(
            rows,
            key=lambda row: (
                0 if str(row.get("epd_status")) == "validated" else 1,
                float(row.get("goal_distance") if row.get("goal_distance") is not None else float("inf")),
                -int(row.get("round_index") or 0),
            ),
        )[:limit]
        recent = sorted(rows, key=lambda row: int(row.get("round_index") or 0), reverse=True)[:limit]
        selected = {str(row.get("record_id") or ""): row for row in [*ranked, *recent]}
        ordered = sorted(selected.values(), key=lambda row: int(row.get("round_index") or 0), reverse=True)
        return {
            "schema_version": "goalevolve.v2.epd.teacher-summary.v1",
            "full_epd_artifact": str(self.path),
            "quarantine_artifact": str(self.path.parent / "evidence_quarantine.json"),
            "quarantined_record_count": len(self.records()) - len(all_rows),
            "baseline_record_count": len(all_rows) - len(rows),
            "status_counts": counts,
            "record_count": len(rows),
            "decision_records": [compact(row) for row in ordered[:limit]],
        }

    @staticmethod
    def _checkpoint_metrics(value: str | None) -> dict[str, dict[str, float]]:
        if not value:
            return {}
        payload = load_json(Path(value), {}) or {}
        raw = payload.get("checkpoints") if isinstance(payload, dict) else {}
        return {
            str(stage): {str(name): float(metric) for name, metric in dict(metrics).items() if isinstance(metric, (int, float))}
            for stage, metrics in dict(raw or {}).items()
            if isinstance(metrics, dict)
        }
