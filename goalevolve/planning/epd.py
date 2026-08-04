from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..core.io import atomic_json, canonical_json, load_json, sha256_json
from ..core.models import CandidateResult, EvidenceVerdict, Parent


EPD_STATUSES = ("validated", "promising", "pending", "unactivated", "invalid")
EPD_SCHEMA_VERSION = "goalevolve.v2.epd.v2"
EPD_PROJECTION_SCHEMA_VERSION = "goalevolve.v2.epd.projection.v1"
DEFAULT_MAX_REINFORCEMENT_ATTEMPTS = 2


@dataclass(frozen=True)
class EPDRecord:
    """An immutable, actually executed Student attempt in the EPD."""

    record_id: str
    idea_id: str
    parent_id: str
    source_hash: str
    hypothesis_id: str
    mechanism_family: str
    epd_status: str
    evidence_state: str
    executed: bool
    metrics: dict[str, float]
    phase_signals: dict[str, float]
    goal_distance: float | None
    distance_gain: float | None
    checkpoint_metrics: dict[str, dict[str, float]]
    artifacts: dict[str, str]
    source_hooks: tuple[str, ...]
    expected_signals: tuple[str, ...]
    source_change_bundle: dict[str, object]
    student_role: str
    role_mode: str
    epd_record_ids: tuple[str, ...]
    round_index: int
    updated_at: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def epd_status(verdict: EvidenceVerdict, candidate: CandidateResult) -> str:
    """Map controller evidence into the durable lifecycle state."""
    if verdict.state == "validated":
        return "validated"
    if verdict.state in {"local_only", "verified_qor_unattributed"} and verdict.distance_gain > 0:
        return "promising"
    if verdict.integrity_ok and not verdict.mechanism_fired:
        # The flow and its QoR are real evidence, but the edited decision
        # path did not execute. It must not suppress the Teacher mechanism
        # before a recipe that reaches the hook has been evaluated.
        return "unactivated"
    # ``pending`` belongs to a Teacher idea that has not reached a Student.
    # Once a Student has executed a flow, an incomplete engineering result is
    # not executable EPD evidence and therefore cannot become a role parent.
    return "invalid"


class EvolutionProgramDatabase:
    """Persistent idea lineage plus immutable source-backed execution attempts.

    ``ideas`` is the Teacher's evolving mechanism database. ``attempts`` is
    the evidence ledger: only it may provide an implementation diff to an
    Integrator or Enhancer. This separation prevents an unexecuted pending
    idea from being mistaken for an inherited source patch.
    """

    def __init__(
        self,
        state_root: Path,
        *,
        max_reinforcement_attempts: int = DEFAULT_MAX_REINFORCEMENT_ATTEMPTS,
    ) -> None:
        self.path = state_root / "knowledge" / "epd.json"
        self.projection_root = self.path.parent / "epd"
        self.max_reinforcement_attempts = max(0, int(max_reinforcement_attempts))

    def _payload(self) -> dict[str, object]:
        raw = load_json(self.path, {}) or {}
        if not isinstance(raw, dict):
            raw = {}
        if str(raw.get("schema_version") or "") == EPD_SCHEMA_VERSION:
            return self._normalized_payload(raw)
        return self._migrate_v1_payload(raw)

    @staticmethod
    def _normalized_payload(raw: Mapping[str, object]) -> dict[str, object]:
        return {
            "schema_version": EPD_SCHEMA_VERSION,
            "statuses": list(EPD_STATUSES),
            "ideas": [dict(item) for item in list(raw.get("ideas") or ()) if isinstance(item, Mapping)],
            "attempts": [dict(item) for item in list(raw.get("attempts") or ()) if isinstance(item, Mapping)],
        }

    def _migrate_v1_payload(self, raw: Mapping[str, object]) -> dict[str, object]:
        """Expose legacy records as v2 ideas/attempts without losing evidence."""
        legacy = [dict(item) for item in list(raw.get("records") or ()) if isinstance(item, Mapping)]
        ideas: list[dict[str, object]] = []
        attempts: list[dict[str, object]] = []
        for row in legacy:
            record_id = str(row.get("record_id") or "")
            if not record_id:
                continue
            idea_id = str(row.get("idea_id") or self._idea_id_for_legacy(row))
            if not any(str(idea.get("idea_id") or "") == idea_id for idea in ideas):
                status = str(row.get("epd_status") or "invalid")
                ideas.append(
                    self._idea_row(
                        idea_id=idea_id,
                        idea=str(row.get("claim") or row.get("hypothesis_id") or row.get("mechanism_family") or "legacy attempt"),
                        round_index=int(row.get("round_index") or 0),
                        parent_id=str(row.get("parent_id") or ""),
                        source_hooks=tuple(str(path) for path in list(row.get("source_hooks") or ()) if path),
                        expected_signals=tuple(str(signal) for signal in list(row.get("expected_signals") or ()) if signal),
                        status=status,
                    )
                )
            migrated = dict(row)
            migrated["idea_id"] = idea_id
            migrated["executed"] = True
            migrated.setdefault("phase_signals", {})
            migrated.setdefault("source_change_bundle", self._source_change_bundle("", row))
            attempts.append(migrated)
        self._refresh_ideas(ideas=ideas, attempts=attempts)
        return {"schema_version": EPD_SCHEMA_VERSION, "statuses": list(EPD_STATUSES), "ideas": ideas, "attempts": attempts}

    @staticmethod
    def _idea_id_for_legacy(row: Mapping[str, object]) -> str:
        return "IDEA_" + sha256_json(
            {
                "hypothesis": row.get("hypothesis_id"),
                "family": row.get("mechanism_family"),
                "hooks": list(row.get("source_hooks") or ()),
            }
        )[:16]

    def _idea_row(
        self,
        *,
        idea_id: str,
        idea: str,
        round_index: int,
        parent_id: str,
        source_hooks: Sequence[str] = (),
        expected_signals: Sequence[str] = (),
        predicted_stage_effect: str = "",
        source_evidence: Sequence[str] = (),
        falsification_condition: str = "",
        paper_card_ids: Sequence[str] = (),
        teacher_priority: int = 0,
        diagnosis_summary: str = "",
        parent_policy: str = "",
        stage: str = "",
        mechanism_family: str = "",
        observed_state: str = "",
        decision_boundary: str = "",
        proposed_action: str = "",
        acceptance_or_rollback_rule: str = "",
        status: str = "pending",
    ) -> dict[str, object]:
        return {
            "idea_id": idea_id,
            "idea": idea,
            "predicted_stage_effect": predicted_stage_effect,
            "source_evidence": list(source_evidence),
            "falsification_condition": falsification_condition,
            "paper_card_ids": list(paper_card_ids),
            "expected_signals": list(expected_signals),
            "source_hooks": list(source_hooks),
            "teacher_priority": int(teacher_priority),
            "proposed_round": int(round_index),
            "parent_id": parent_id,
            "diagnosis_summary": diagnosis_summary,
            "parent_policy": parent_policy,
            "stage": stage,
            "mechanism_family": mechanism_family,
            "observed_state": observed_state,
            "decision_boundary": decision_boundary,
            "proposed_action": proposed_action,
            "acceptance_or_rollback_rule": acceptance_or_rollback_rule,
            "status": status,
            "executed": False,
            "execution_count": 0,
            "reinforcement_attempts": 0,
            "max_reinforcement_attempts": self.max_reinforcement_attempts,
            "inherited": False,
            "inherited_parent_id": "",
            "inherited_source_hash": "",
            "parent_idea_ids": [],
            "attempt_ids": [],
            "terminal_reason": "",
            "updated_at": int(time.time()),
        }

    def _write(self, payload: Mapping[str, object]) -> None:
        normalized = self._normalized_payload(payload)
        normalized["ideas"] = sorted(
            normalized["ideas"],
            key=lambda item: (int(item.get("proposed_round") or 0), str(item.get("idea_id") or "")),
        )
        normalized["attempts"] = sorted(
            normalized["attempts"],
            key=lambda item: (int(item.get("round_index") or 0), str(item.get("record_id") or "")),
        )
        atomic_json(self.path, normalized)
        self._project(normalized)

    def rebuild_projection(self) -> Path:
        """Materialize path-addressable EPD objects from compatible ``epd.json``."""
        self._project(self._payload())
        return self.projection_root / "manifest.json"

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _text_from_artifact(path: object, *, fallback: str = "") -> str:
        candidate = Path(str(path or ""))
        try:
            return candidate.read_text(encoding="utf-8") if candidate.is_file() else fallback
        except OSError:
            return fallback

    @staticmethod
    def _mechanism_id(idea: Mapping[str, object], attempt: Mapping[str, object]) -> str:
        return "MECH_" + sha256_json(
            {
                "family": attempt.get("mechanism_family"),
                "hooks": list(attempt.get("source_hooks") or ()),
                "boundary": idea.get("decision_boundary"),
            }
        )[:16]

    @staticmethod
    def _jsonl(rows: Sequence[Mapping[str, object]]) -> str:
        return "".join(canonical_json(dict(row)) + "\n" for row in rows)

    def _project(self, payload: Mapping[str, object]) -> None:
        """Write a rebuildable object/index view without changing EPD authority."""
        root = self.projection_root
        ideas = [dict(row) for row in list(payload.get("ideas") or ()) if isinstance(row, Mapping)]
        attempts = [dict(row) for row in list(payload.get("attempts") or ()) if isinstance(row, Mapping)]
        ideas_by_id = {str(row.get("idea_id") or ""): row for row in ideas}
        attempts_by_idea: dict[str, list[dict[str, object]]] = {}
        for attempt in attempts:
            attempts_by_idea.setdefault(str(attempt.get("idea_id") or ""), []).append(attempt)

        catalog: list[dict[str, object]] = []
        corpus: list[dict[str, object]] = []
        for idea in sorted(ideas, key=lambda row: str(row.get("idea_id") or "")):
            idea_id = str(idea.get("idea_id") or "")
            if not idea_id:
                continue
            idea_path = root / "ideas" / idea_id / "idea.json"
            attempt_ids = [
                str(attempt.get("record_id") or "")
                for attempt in attempts_by_idea.get(idea_id, [])
                if str(attempt.get("record_id") or "")
            ]
            rendered = {
                **idea,
                "attempt_ids": attempt_ids or list(idea.get("attempt_ids") or ()),
                "idea_path": str(idea_path),
            }
            atomic_json(idea_path, rendered)
            catalog.append(
                {
                    "idea_id": idea_id,
                    "status": str(idea.get("status") or "pending"),
                    "stage": str(idea.get("stage") or ""),
                    "mechanism_family": str(idea.get("mechanism_family") or ""),
                    "source_hooks": list(idea.get("source_hooks") or ()),
                    "decision_boundary": str(idea.get("decision_boundary") or ""),
                    "idea_path": str(idea_path),
                    "attempt_ids": attempt_ids,
                }
            )
            corpus.append(
                {
                    "idea_id": idea_id,
                    "stage": str(idea.get("stage") or ""),
                    "source_hooks": list(idea.get("source_hooks") or ()),
                    "decision_boundary": str(idea.get("decision_boundary") or ""),
                    "text": "\n".join(
                        str(idea.get(name) or "")
                        for name in (
                            "idea",
                            "diagnosis_summary",
                            "observed_state",
                            "decision_boundary",
                            "proposed_action",
                            "acceptance_or_rollback_rule",
                            "falsification_condition",
                        )
                    ).strip(),
                    "idea_path": str(idea_path),
                }
            )

        mechanism_rows: dict[str, dict[str, object]] = {}
        for attempt in sorted(attempts, key=lambda row: str(row.get("record_id") or "")):
            record_id = str(attempt.get("record_id") or "")
            if not record_id:
                continue
            idea = ideas_by_id.get(str(attempt.get("idea_id") or ""), {})
            attempt_root = root / "attempts" / record_id
            artifacts = dict(attempt.get("artifacts") or {})
            reflection_path = attempt_root / "student_reflection.md"
            diff_path = attempt_root / "implementation.diff"
            stage_path = attempt_root / "stage_metrics.json"
            signals_path = attempt_root / "phase_signals.json"
            evidence_path = attempt_root / "evidence_manifest.json"
            local_artifacts = {
                "attempt": str(attempt_root / "attempt.json"),
                "stage_metrics": str(stage_path),
                "phase_signals": str(signals_path),
                "student_reflection": str(reflection_path),
                "implementation_diff": str(diff_path),
                "evidence_manifest": str(evidence_path),
            }
            self._atomic_text(
                reflection_path,
                self._text_from_artifact(
                    artifacts.get("student_reflection"),
                    fallback="Student reflection was not recorded for this historical attempt.\n",
                ),
            )
            self._atomic_text(
                diff_path,
                self._text_from_artifact(
                    artifacts.get("implementation_diff"),
                    fallback=str(attempt.get("implementation_diff") or ""),
                ),
            )
            atomic_json(
                stage_path,
                {
                    "metrics_after": dict(attempt.get("metrics") or {}),
                    "checkpoint_effects": dict(attempt.get("checkpoint_metrics") or {}),
                },
            )
            atomic_json(signals_path, dict(attempt.get("phase_signals") or {}))
            atomic_json(evidence_path, {"record_id": record_id, "source_artifacts": artifacts, "local_artifacts": local_artifacts})
            atomic_json(
                attempt_root / "attempt.json",
                {**attempt, "artifacts": {**artifacts, **local_artifacts}},
            )

            mechanism_id = self._mechanism_id(idea, attempt)
            card = mechanism_rows.setdefault(
                mechanism_id,
                {
                    "mechanism_id": mechanism_id,
                    "origin_idea_ids": [],
                    "attempt_ids": [],
                    "status": "invalid",
                    "stage": str(idea.get("stage") or ""),
                    "mechanism_summary": str(idea.get("idea") or attempt.get("mechanism_family") or ""),
                    "decision_boundary": str(idea.get("decision_boundary") or ""),
                    "source_hooks": [],
                    "state_read_set": [str(idea.get("observed_state") or "")],
                    "source_write_set": [],
                    "action_type": str(idea.get("proposed_action") or "source_policy_change"),
                    "commit_scope": "candidate",
                    "dependencies": [],
                    "known_conflicts": [],
                    "parent_compatibility": [],
                    "observed_qor_effects": [],
                    "downstream_retention": [],
                    "student_reflection_paths": [],
                    "implementation_artifact_paths": [],
                },
            )
            card["origin_idea_ids"] = sorted(set([*card["origin_idea_ids"], str(attempt.get("idea_id") or "")]))
            card["attempt_ids"] = sorted(set([*card["attempt_ids"], record_id]))
            card["source_hooks"] = sorted(set([*card["source_hooks"], *[str(item) for item in list(attempt.get("source_hooks") or ()) if item]]))
            bundle = dict(attempt.get("source_change_bundle") or {})
            card["source_write_set"] = sorted(set([*card["source_write_set"], *[str(item) for item in list(bundle.get("modified_files") or ()) if item]]))
            card["parent_compatibility"].append(str(attempt.get("parent_id") or ""))
            card["observed_qor_effects"].append({"record_id": record_id, "metrics": dict(attempt.get("metrics") or {}), "distance_gain": attempt.get("distance_gain")})
            card["downstream_retention"].append({"record_id": record_id, "checkpoint_effects": dict(attempt.get("checkpoint_metrics") or {})})
            card["student_reflection_paths"].append(str(reflection_path))
            card["implementation_artifact_paths"].append(str(diff_path))
            status = str(attempt.get("epd_status") or "invalid")
            if EPD_STATUSES.index(status) < EPD_STATUSES.index(str(card["status"])):
                card["status"] = status

        mechanism_manifest: list[dict[str, object]] = []
        for mechanism_id, card in sorted(mechanism_rows.items()):
            card["state_read_set"] = [item for item in card["state_read_set"] if item]
            card["parent_compatibility"] = sorted(set(item for item in card["parent_compatibility"] if item))
            card["student_reflection_paths"] = sorted(set(card["student_reflection_paths"]))
            card["implementation_artifact_paths"] = sorted(set(card["implementation_artifact_paths"]))
            card_path = root / "mechanisms" / mechanism_id / "mechanism_card.json"
            atomic_json(card_path, card)
            mechanism_manifest.append({"mechanism_id": mechanism_id, "path": str(card_path), "status": card["status"]})

        indexes = root / "indexes"
        self._atomic_text(indexes / "idea_catalog.jsonl", self._jsonl(catalog))
        self._atomic_text(indexes / "retrieval_corpus.jsonl", self._jsonl(corpus))
        for status in EPD_STATUSES:
            atomic_json(indexes / "by_status" / f"{status}.json", [row for row in catalog if row["status"] == status])
        for key, field in (("by_stage", "stage"), ("by_source_hook", "source_hooks")):
            groups: dict[str, list[str]] = {}
            for row in catalog:
                values = row[field] if field == "source_hooks" else [row[field]]
                for value in values:
                    if value:
                        groups.setdefault(str(value), []).append(str(row["idea_id"]))
            for value, idea_ids in groups.items():
                atomic_json(indexes / key / f"{sha256_json(value)[:16]}.json", {"key": value, "idea_ids": sorted(idea_ids)})

        rounds = sorted({int(row.get("proposed_round") or 0) for row in ideas if int(row.get("proposed_round") or 0) > 0})
        for round_index in rounds:
            round_root = root / "round_views" / f"round_{round_index:03d}"
            round_ideas = [row for row in catalog if int(ideas_by_id[str(row["idea_id"])].get("proposed_round") or 0) == round_index]
            atomic_json(round_root / "explorer_view.json", {"round_index": round_index, "epd_root": str(root), "idea_catalog": str(indexes / "idea_catalog.jsonl"), "retrieval_corpus": str(indexes / "retrieval_corpus.jsonl"), "full_manifest": str(root / "manifest.json"), "search_tool": "python -m goalevolve.epd_search", "ideas": round_ideas})
            atomic_json(round_root / "enhancer_view.json", {"round_index": round_index, "candidates": [row for row in mechanism_manifest if row["status"] == "promising"]})
            atomic_json(round_root / "integrator_view.json", {"round_index": round_index, "candidates": [row for row in mechanism_manifest if row["status"] in {"validated", "promising"}]})

        atomic_json(
            root / "manifest.json",
            {
                "schema_version": EPD_PROJECTION_SCHEMA_VERSION,
                "compatibility_epd": str(self.path),
                "epd_root": str(root),
                "idea_catalog": str(indexes / "idea_catalog.jsonl"),
                "retrieval_corpus": str(indexes / "retrieval_corpus.jsonl"),
                "idea_count": len(catalog),
                "attempt_count": len(attempts),
                "mechanisms": mechanism_manifest,
            },
        )

    def records(self) -> list[dict[str, object]]:
        """Compatibility name for immutable executed attempt records."""
        return [dict(item) for item in list(self._payload().get("attempts") or ())]

    def ideas(self) -> list[dict[str, object]]:
        return [dict(item) for item in list(self._payload().get("ideas") or ())]

    def idea(self, idea_id: str) -> dict[str, object]:
        match = next((row for row in self.ideas() if str(row.get("idea_id") or "") == idea_id), None)
        if match is None:
            raise KeyError(f"unknown EPD idea: {idea_id}")
        return match

    def register_teacher_ideas(
        self,
        *,
        round_index: int,
        parent: Parent,
        evolution_ideas: Sequence[object],
        diagnosis_summary: str = "",
        parent_policy: str = "",
    ) -> tuple[str, ...]:
        """Persist every Teacher idea before the controller assigns Students."""
        payload = self._payload()
        ideas = list(payload["ideas"])
        created: list[str] = []
        for priority, raw in enumerate(evolution_ideas):
            source = dict(raw) if isinstance(raw, Mapping) else {}
            idea = str(source.get("idea") if source else raw).strip()
            if not idea:
                continue
            key = self._idea_key(idea, parent.parent_id)
            existing = next(
                (row for row in ideas if self._idea_key(str(row.get("idea") or ""), str(row.get("parent_id") or "")) == key),
                None,
            )
            if existing is not None:
                created.append(str(existing["idea_id"]))
                continue
            idea_id = "IDEA_" + sha256_json(
                {"parent": parent.parent_id, "round": round_index, "idea": idea, "priority": priority}
            )[:16]
            ideas.append(
                self._idea_row(
                    idea_id=idea_id,
                    idea=idea,
                    round_index=round_index,
                    parent_id=parent.parent_id,
                    source_hooks=tuple(str(path) for path in list(source.get("source_hooks") or ()) if path),
                    expected_signals=tuple(str(signal) for signal in list(source.get("expected_signals") or ()) if signal),
                    predicted_stage_effect=str(source.get("predicted_stage_effect") or ""),
                    source_evidence=tuple(str(item) for item in list(source.get("source_evidence") or ()) if item),
                    falsification_condition=str(source.get("falsification_condition") or ""),
                    paper_card_ids=tuple(str(item) for item in list(source.get("paper_card_ids") or ()) if item),
                    teacher_priority=self._priority(source.get("priority"), fallback=priority),
                    diagnosis_summary=diagnosis_summary,
                    parent_policy=parent_policy,
                    stage=str(source.get("stage") or ""),
                    mechanism_family=str(source.get("mechanism_family") or ""),
                    observed_state=str(source.get("observed_state") or ""),
                    decision_boundary=str(source.get("decision_boundary") or ""),
                    proposed_action=str(source.get("proposed_action") or ""),
                    acceptance_or_rollback_rule=str(source.get("acceptance_or_rollback_rule") or ""),
                )
            )
            created.append(idea_id)
        payload["ideas"] = ideas
        self._write(payload)
        return tuple(created)

    @staticmethod
    def _priority(value: object, *, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _idea_key(idea: str, parent_id: str) -> str:
        return " ".join(idea.lower().split()) + "\n" + parent_id

    def ensure_idea_for_hypothesis(
        self,
        *,
        round_index: int,
        parent: Parent,
        hypothesis,
    ) -> str:
        if hypothesis.epd_idea_id:
            return hypothesis.epd_idea_id
        payload = self._payload()
        ideas = list(payload["ideas"])
        key = self._idea_key(hypothesis.claim, parent.parent_id)
        existing = next(
            (row for row in ideas if self._idea_key(str(row.get("idea") or ""), str(row.get("parent_id") or "")) == key),
            None,
        )
        if existing is not None:
            return str(existing["idea_id"])
        idea_id = "IDEA_" + sha256_json(
            {
                "parent": parent.parent_id,
                "hypothesis": hypothesis.hypothesis_id,
                "claim": hypothesis.claim,
                "hooks": hypothesis.source_hooks,
            }
        )[:16]
        parent_ideas = self._parent_idea_ids(hypothesis.epd_record_ids, payload.get("attempts") or ())
        if (
            hypothesis.student_role == "enhancer"
            and hypothesis.role_mode == "epd_enhancement"
            and len(parent_ideas) == 1
        ):
            # Reinforcement is a continuation of the promising mechanism, not
            # a fresh lineage. Its bounded budget must follow that idea.
            return parent_ideas[0]
        ideas.append(
            self._idea_row(
                idea_id=idea_id,
                idea=hypothesis.claim,
                round_index=round_index,
                parent_id=parent.parent_id,
                source_hooks=hypothesis.source_hooks,
                expected_signals=hypothesis.expected_signals,
            )
        )
        ideas[-1]["parent_idea_ids"] = parent_ideas
        payload["ideas"] = ideas
        self._write(payload)
        return idea_id

    def retire_pending_ideas(
        self,
        *,
        idea_ids: Sequence[str],
        reason: str = "teacher_retired_pending",
    ) -> tuple[str, ...]:
        """Retire only unexecuted pending ideas while preserving all evidence.

        Teacher may change the future exploration queue, but completed Student
        attempts are an immutable experiment ledger.  Therefore an unknown,
        executed, or already-terminal idea is ignored rather than deleted.
        """
        requested = tuple(dict.fromkeys(str(item).strip() for item in idea_ids if str(item).strip()))
        if not requested:
            return ()
        payload = self._payload()
        requested_set = set(requested)
        retired: list[str] = []
        for idea in list(payload.get("ideas") or ()):
            idea_id = str(idea.get("idea_id") or "")
            if idea_id not in requested_set:
                continue
            if str(idea.get("status") or "pending") != "pending":
                continue
            if bool(idea.get("executed")) or int(idea.get("execution_count") or 0) > 0:
                continue
            idea["status"] = "invalid"
            idea["terminal_reason"] = reason.strip() or "teacher_retired_pending"
            idea["updated_at"] = int(time.time())
            retired.append(idea_id)
        if retired:
            self._write(payload)
        return tuple(retired)

    @staticmethod
    def _parent_idea_ids(record_ids: Sequence[str], attempts: Sequence[Mapping[str, object]]) -> list[str]:
        by_id = {str(row.get("record_id") or ""): row for row in attempts}
        return list(
            dict.fromkeys(
                str(by_id[record_id].get("idea_id") or "")
                for record_id in record_ids
                if record_id in by_id and str(by_id[record_id].get("idea_id") or "")
            )
        )

    def record(
        self,
        *,
        round_index: int,
        parent: Parent,
        candidate: CandidateResult,
        verdict: EvidenceVerdict,
    ) -> EPDRecord:
        """Append one real Student attempt and update its durable idea state."""
        payload = self._payload()
        idea_id = self.ensure_idea_for_hypothesis(
            round_index=round_index,
            parent=parent,
            hypothesis=candidate.hypothesis,
        )
        # ensure_idea_for_hypothesis may have written a fresh payload.
        payload = self._payload()
        checkpoints = self._checkpoint_metrics(candidate.artifacts.get("checkpoint_metrics"))
        record_id = "EPD_" + sha256_json(
            {"source": candidate.source_commit, "hypothesis": candidate.hypothesis.hypothesis_id}
        )[:16]
        artifacts = self._persist_candidate_artifacts(record_id=record_id, candidate=candidate)
        bundle = self._source_change_bundle(candidate.implementation_diff, candidate.hypothesis.to_dict())
        record = EPDRecord(
            record_id=record_id,
            idea_id=idea_id,
            parent_id=parent.parent_id,
            source_hash=candidate.source_commit,
            hypothesis_id=candidate.hypothesis.hypothesis_id,
            mechanism_family=candidate.hypothesis.mechanism_family,
            epd_status=epd_status(verdict, candidate),
            evidence_state=verdict.state,
            executed=True,
            metrics=dict(candidate.metrics),
            phase_signals={str(name): float(value) for name, value in candidate.phase_signals.items()},
            goal_distance=None if verdict.goal_distance == float("inf") else verdict.goal_distance,
            distance_gain=verdict.distance_gain,
            checkpoint_metrics=checkpoints,
            artifacts=artifacts,
            source_hooks=tuple(candidate.hypothesis.source_hooks),
            expected_signals=tuple(candidate.hypothesis.expected_signals),
            source_change_bundle=bundle,
            student_role=candidate.hypothesis.student_role,
            role_mode=candidate.hypothesis.role_mode,
            epd_record_ids=tuple(candidate.hypothesis.epd_record_ids),
            round_index=round_index,
            updated_at=int(time.time()),
        )
        existing = {
            str(item.get("record_id") or ""): item
            for item in list(payload.get("attempts") or ())
        }
        existing[record.record_id] = record.to_dict()
        payload["attempts"] = list(existing.values())
        self._refresh_ideas(ideas=list(payload.get("ideas") or ()), attempts=list(payload["attempts"]))
        self._write(payload)
        return record

    def reclassify_historical_unactivated_attempts(self) -> tuple[str, ...]:
        """Repair lifecycle labels from campaigns written before ``unactivated``.

        Old records contain the same independent evidence ledger. Reclassify
        only complete, non-firing invalid attempts, then refresh their ideas;
        no source, metric, verdict, or artifact is changed.
        """
        payload = self._payload()
        changed: list[str] = []
        for attempt in list(payload.get("attempts") or ()):
            if str(attempt.get("epd_status") or "") != "invalid":
                continue
            if str(attempt.get("evidence_state") or "") != "refuted":
                continue
            expected = tuple(str(item) for item in list(attempt.get("expected_signals") or ()) if str(item))
            signals = {
                str(name): float(value)
                for name, value in dict(attempt.get("phase_signals") or {}).items()
                if isinstance(value, (int, float))
            }
            artifacts = dict(attempt.get("artifacts") or {})
            if not expected or not all(abs(signals.get(signal, 0.0)) == 0.0 for signal in expected):
                continue
            if not self._complete_official_artifacts(artifacts):
                continue
            attempt["epd_status"] = "unactivated"
            changed.append(str(attempt.get("record_id") or ""))
        if changed:
            self._refresh_ideas(
                ideas=list(payload.get("ideas") or ()),
                attempts=list(payload.get("attempts") or ()),
            )
            self._write(payload)
        return tuple(record_id for record_id in changed if record_id)

    @staticmethod
    def _complete_official_artifacts(artifacts: Mapping[str, object]) -> bool:
        if all(
            Path(str(artifacts.get(name) or "")).is_file()
            for name in ("evaluation_log", "metrics_csv", "official_4of4_log", "implementation_diff")
        ):
            return True
        # Every EPD record persists a candidate snapshot. Older campaigns did
        # not always retain the individual flow-path aliases, but this snapshot
        # still contains the authoritative four check results.
        candidate_path = Path(str(artifacts.get("candidate_artifact") or ""))
        candidate = load_json(candidate_path, {}) or {}
        checks = {
            str(row.get("name") or ""): bool(row.get("passed"))
            for row in list(candidate.get("checks") or ())
            if isinstance(row, Mapping)
        }
        return (
            not candidate.get("evaluation_error")
            and bool(str(candidate.get("implementation_diff") or "").strip())
            and bool(str(candidate.get("source_commit") or "").strip())
            and all(checks.get(name, False) for name in ("build", "flow", "metrics", "lec"))
        )

    def mark_inherited(self, *, record_id: str, parent: Parent) -> None:
        payload = self._payload()
        attempt = next(
            (row for row in list(payload["attempts"]) if str(row.get("record_id") or "") == record_id),
            None,
        )
        if attempt is None:
            return
        idea_id = str(attempt.get("idea_id") or "")
        for idea in list(payload["ideas"]):
            if str(idea.get("idea_id") or "") != idea_id:
                continue
            idea["inherited"] = True
            idea["inherited_parent_id"] = parent.parent_id
            idea["inherited_source_hash"] = parent.source_hash
            idea["updated_at"] = int(time.time())
            break
        self._write(payload)

    def ensure_baseline(self, parent: Parent) -> None:
        payload = self._payload()
        record_id = f"EPD_baseline_{parent.source_hash[:16]}"
        if any(str(row.get("record_id") or "") == record_id for row in list(payload["attempts"])):
            return
        idea_id = "IDEA_baseline_" + parent.source_hash[:16]
        if not any(str(row.get("idea_id") or "") == idea_id for row in list(payload["ideas"])):
            payload["ideas"].append(
                self._idea_row(
                    idea_id=idea_id,
                    idea="baseline",
                    round_index=0,
                    parent_id=parent.parent_id,
                    status="validated",
                )
            )
        payload["attempts"].append(
            EPDRecord(
                record_id=record_id,
                idea_id=idea_id,
                parent_id=parent.parent_id,
                source_hash=parent.source_hash,
                hypothesis_id="baseline",
                mechanism_family="baseline",
                epd_status="pending",
                evidence_state="baseline_configured_metrics",
                executed=True,
                metrics=dict(parent.metrics),
                phase_signals={},
                goal_distance=parent.goal_distance,
                distance_gain=None,
                checkpoint_metrics={},
                artifacts={},
                source_hooks=(),
                expected_signals=(),
                source_change_bundle={},
                student_role="baseline",
                role_mode="baseline",
                epd_record_ids=(),
                round_index=0,
                updated_at=int(time.time()),
            ).to_dict()
        )
        self._refresh_ideas(ideas=list(payload["ideas"]), attempts=list(payload["attempts"]))
        self._write(payload)

    def attach_baseline_evaluation(
        self,
        *,
        parent: Parent,
        metrics: dict[str, float],
        goal_distance: float,
        artifacts: dict[str, str],
        passed: bool,
    ) -> None:
        self.ensure_baseline(parent)
        payload = self._payload()
        record_id = f"EPD_baseline_{parent.source_hash[:16]}"
        for row in list(payload["attempts"]):
            if str(row.get("record_id") or "") != record_id:
                continue
            row.update(
                {
                    "epd_status": "validated" if passed else "pending",
                    "evidence_state": "baseline_measured_4of4" if passed else "baseline_measurement_incomplete",
                    "metrics": dict(metrics),
                    "phase_signals": {},
                    "goal_distance": goal_distance,
                    "checkpoint_metrics": self._checkpoint_metrics(artifacts.get("checkpoint_metrics")),
                    "artifacts": dict(artifacts),
                    "updated_at": int(time.time()),
                }
            )
        self._refresh_ideas(ideas=list(payload["ideas"]), attempts=list(payload["attempts"]))
        self._write(payload)

    def summary(self, *, limit: int = 12) -> dict[str, object]:
        ideas = [row for row in self.ideas() if str(row.get("idea") or "") != "baseline"]
        attempts = [row for row in self.records() if str(row.get("mechanism_family") or "") != "baseline"]
        counts = {status: 0 for status in EPD_STATUSES}
        for idea in ideas:
            status = str(idea.get("status") or "invalid")
            if status in counts:
                counts[status] += 1
        ordered = sorted(
            attempts,
            key=lambda row: (
                float(row.get("goal_distance") if row.get("goal_distance") is not None else float("inf")),
                -float(row.get("distance_gain") or 0.0),
            ),
        )
        return {
            "schema_version": EPD_SCHEMA_VERSION,
            "status_counts": counts,
            "idea_count": len(ideas),
            "attempt_count": len(attempts),
            "record_count": len(attempts),
            "pending_idea_count": counts["pending"],
            "baseline_record_count": len(self.records()) - len(attempts),
            "total_record_count": len(self.records()),
            "recent_records": ordered[:limit],
        }

    def teacher_summary(self, *, limit: int = 16) -> dict[str, object]:
        quarantine = load_json(self.path.parent / "evidence_quarantine.json", {}) or {}
        excluded = {str(item) for item in list(quarantine.get("hypothesis_ids") or [])}
        attempts = [
            row
            for row in self.records()
            if str(row.get("hypothesis_id") or "") not in excluded
            and str(row.get("mechanism_family") or "") != "baseline"
        ]
        ideas = [row for row in self.ideas() if str(row.get("idea") or "") != "baseline"]
        counts = {status: 0 for status in EPD_STATUSES}
        for idea in ideas:
            status = str(idea.get("status") or "invalid")
            if status in counts:
                counts[status] += 1
        ranked_attempts = sorted(
            attempts,
            key=lambda row: (
                0 if str(row.get("epd_status") or "") == "validated" else 1,
                float(row.get("goal_distance") if row.get("goal_distance") is not None else float("inf")),
                -int(row.get("round_index") or 0),
            ),
        )[:limit]
        pending = sorted(
            (row for row in ideas if str(row.get("status") or "") == "pending"),
            key=lambda row: (int(row.get("teacher_priority") or 0), -int(row.get("proposed_round") or 0)),
        )[:limit]
        return {
            "schema_version": EPD_SCHEMA_VERSION,
            "full_epd_artifact": str(self.path),
            "status_counts": counts,
            "baseline_record_count": len(self.records()) - len(attempts),
            "pending_ideas": pending,
            "decision_records": [self._compact_attempt(row) for row in ranked_attempts],
        }

    def role_portfolio(self, *, contract, parent: Parent, limit: int = 12) -> dict[str, object]:
        """Build role candidates from actual attempts, never pending prose ideas.

        An Integrator combines only two distinct validated mechanisms that
        are not already part of the current parent's source lineage. An
        Enhancer is deliberately narrower in another direction: only a
        promising attempt has an unresolved but positive result worth spending
        its bounded reinforcement budget on.
        """
        ideas_by_id = {
            str(idea.get("idea_id") or ""): idea
            for idea in self.ideas()
            if str(idea.get("idea_id") or "")
        }
        lineage_parent_ids, lineage_source_hashes = self._current_parent_lineage(parent)
        attempts = []
        for row in self.records():
            if str(row.get("mechanism_family") or "") == "baseline":
                continue
            status = str(row.get("epd_status") or "invalid")
            if status in {"invalid", "unactivated"} or not bool(row.get("executed", True)):
                continue
            normalized = self._role_record(row=row, contract=contract, parent=parent)
            if normalized is not None:
                attempts.append(normalized)
        descendant_stats = self._descendant_stats()
        for row in attempts:
            stats = descendant_stats.get(str(row["record_id"]), {})
            row.update(stats)
            row["elite_score"] = (
                float(row["distance_gain"])
                + 0.10 * int(stats.get("offspring_validated_count") or 0)
                - 0.03 * int(stats.get("offspring_invalid_count") or 0)
                + 0.02 * max(float(stats.get("best_offspring_distance_gain") or 0.0), 0.0)
            )
        attempts.sort(
            key=lambda row: (
                0 if row["epd_status"] == "validated" else 1,
                -float(row["elite_score"]),
                -float(row["distance_gain"]),
                float(row["goal_distance"]) if isinstance(row["goal_distance"], (int, float)) else float("inf"),
                -int(row["round_index"]),
                str(row["record_id"]),
            )
        )
        integration_records = [
            row
            for row in attempts
            if row["epd_status"] == "validated"
            and not self._idea_is_inherited_by_lineage(
                ideas_by_id.get(str(row.get("idea_id") or ""), {}),
                lineage_parent_ids=lineage_parent_ids,
                lineage_source_hashes=lineage_source_hashes,
            )
        ]
        integration_candidates = self._integration_candidates(integration_records)
        enhancement_candidates = [
            str(row["record_id"])
            for row in attempts
            if row["epd_status"] == "promising" and self._idea_can_reinforce(str(row.get("idea_id") or ""))
        ]
        pending_ideas = self.pending_explorer_ideas(parent=parent, limit=limit)
        return {
            "schema_version": EPD_SCHEMA_VERSION,
            "selection_rule": "Integrator: validated source-backed attempts not inherited by the current parent lineage; Enhancer: promising attempts within the idea reinforcement budget; Explorer: Teacher-ranked pending ideas.",
            # Role construction must retain every eligible source-backed
            # attempt. Teacher summaries remain compact separately; truncating
            # this execution view would silently make old valid records
            # ineligible for integration.
            "records": attempts,
            "islands": {
                island: [str(row["record_id"]) for row in attempts if row["qor_island"] == island]
                for island in sorted({str(row["qor_island"]) for row in attempts})
            },
            "integration_record_ids": integration_candidates[0] if integration_candidates else [],
            "enhancement_record_ids": enhancement_candidates[:1],
            "integration_candidates": integration_candidates,
            "integration_eligible_record_ids": [str(row["record_id"]) for row in integration_records],
            "enhancement_candidates": enhancement_candidates,
            "pending_explorer_ideas": pending_ideas,
        }

    def _current_parent_lineage(self, parent: Parent) -> tuple[set[str], set[str]]:
        """Return recorded parent IDs and source hashes that lead to ``parent``.

        An EPD idea is marked inherited at the first promotion that contains
        it. Later promoted parents have different content hashes, so checking
        only the current hash would accidentally offer already-present source
        changes to an Integrator again.
        """
        parent_ids = {parent.parent_id}
        source_hashes = {parent.source_hash}
        rounds_root = self.path.parent.parent / "rounds"
        summaries = [
            load_json(path, {}) or {}
            for path in sorted(rounds_root.glob("round_*/round.json"), reverse=True)
        ] if rounds_root.is_dir() else []
        changed = True
        while changed:
            changed = False
            for summary in summaries:
                after = dict(summary.get("parent_after") or {})
                after_id = str(after.get("parent_id") or "")
                after_hash = str(after.get("source_hash") or "")
                if after_id not in parent_ids and after_hash not in source_hashes:
                    continue
                before_id = str(summary.get("common_parent_id_at_start") or "")
                before_hash = str(summary.get("common_parent_source_hash_at_start") or "")
                if before_id and before_id not in parent_ids:
                    parent_ids.add(before_id)
                    changed = True
                if before_hash and before_hash not in source_hashes:
                    source_hashes.add(before_hash)
                    changed = True
        return parent_ids, source_hashes

    @staticmethod
    def _idea_is_inherited_by_lineage(
        idea: Mapping[str, object],
        *,
        lineage_parent_ids: set[str],
        lineage_source_hashes: set[str],
    ) -> bool:
        if not bool(idea.get("inherited")):
            return False
        return (
            str(idea.get("inherited_parent_id") or "") in lineage_parent_ids
            or str(idea.get("inherited_source_hash") or "") in lineage_source_hashes
        )

    def pending_explorer_ideas(self, *, parent: Parent, limit: int = 12) -> list[dict[str, object]]:
        return sorted(
            (
                row
                for row in self.ideas()
                if str(row.get("status") or "") == "pending"
                and int(row.get("execution_count") or 0) == 0
            ),
            key=lambda row: (int(row.get("teacher_priority") or 0), -int(row.get("proposed_round") or 0)),
        )[:limit]

    def _role_record(self, *, row: Mapping[str, object], contract, parent: Parent) -> dict[str, object] | None:
        artifacts = dict(row.get("artifacts") or {})
        diff_path = str(artifacts.get("implementation_diff") or self._recover_diff_artifact(artifacts) or "")
        if not diff_path or not Path(diff_path).is_file():
            return None
        metrics = {
            str(name): float(value)
            for name, value in dict(row.get("metrics") or {}).items()
            if isinstance(value, (int, float))
        }
        improvements: list[tuple[float, str]] = []
        for spec in contract.metrics:
            before, after = parent.metrics.get(spec.name), metrics.get(spec.name)
            if before is None or after is None:
                continue
            delta = float(before) - float(after) if spec.minimize else float(after) - float(before)
            scale = max(abs(float(before) - spec.target), abs(float(before)), 1.0)
            improvements.append((delta / scale, spec.name))
        metric_island = max(improvements, default=(0.0, "balanced"), key=lambda item: item[0])[1]
        island = "timing" if any(token in metric_island.lower() for token in ("tns", "wns", "timing")) else (
            "power" if "power" in metric_island.lower() or "leakage" in metric_island.lower() else metric_island
        )
        return {
            "record_id": str(row.get("record_id") or ""),
            "idea_id": str(row.get("idea_id") or ""),
            "hypothesis_id": str(row.get("hypothesis_id") or ""),
            "mechanism_family": str(row.get("mechanism_family") or ""),
            "epd_status": str(row.get("epd_status") or "invalid"),
            "round_index": int(row.get("round_index") or 0),
            "distance_gain": float(row.get("distance_gain") or 0.0),
            "goal_distance": row.get("goal_distance"),
            "metrics": metrics,
            "phase_signals": {
                str(name): float(value)
                for name, value in dict(row.get("phase_signals") or {}).items()
                if isinstance(value, (int, float))
            },
            "qor_island": island,
            "implementation_diff_artifact": diff_path,
            "candidate_source_artifact": str(artifacts.get("candidate_source") or ""),
            "source_hooks": tuple(str(path) for path in list(row.get("source_hooks") or ()) if path),
            "expected_signals": tuple(str(signal) for signal in list(row.get("expected_signals") or ()) if signal),
            "source_change_bundle": dict(row.get("source_change_bundle") or {}),
        }

    def _idea_can_reinforce(self, idea_id: str) -> bool:
        if not idea_id:
            return False
        try:
            idea = self.idea(idea_id)
        except KeyError:
            return False
        return (
            str(idea.get("status") or "") == "promising"
            and int(idea.get("reinforcement_attempts") or 0)
            < int(idea.get("max_reinforcement_attempts") or self.max_reinforcement_attempts)
        )

    @staticmethod
    def _integration_candidates(records: list[dict[str, object]]) -> list[list[str]]:
        """Give every eligible record one bounded two-record integration option."""
        if len(records) < 2:
            return []
        candidates: list[list[str]] = []
        for index, left in enumerate(records):
            partner = next(
                (
                    right
                    for right in records
                    if right is not left and str(right["qor_island"]) != str(left["qor_island"])
                ),
                next(right for right in records if right is not left),
            )
            pair = [str(left["record_id"]), str(partner["record_id"])]
            if pair not in candidates:
                candidates.append(pair)
        return candidates

    def _refresh_ideas(self, *, ideas: list[dict[str, object]], attempts: list[Mapping[str, object]]) -> None:
        now = int(time.time())
        by_idea: dict[str, list[Mapping[str, object]]] = {}
        for attempt in attempts:
            idea_id = str(attempt.get("idea_id") or "")
            if idea_id:
                by_idea.setdefault(idea_id, []).append(attempt)
        for idea in ideas:
            idea_id = str(idea.get("idea_id") or "")
            rows = by_idea.get(idea_id, [])
            if not rows:
                if str(idea.get("idea") or "") == "baseline":
                    idea["executed"] = True
                idea.setdefault("status", "pending")
                idea.setdefault("executed", False)
                idea.setdefault("execution_count", 0)
                idea.setdefault("reinforcement_attempts", 0)
                idea.setdefault("attempt_ids", [])
                idea.setdefault("max_reinforcement_attempts", self.max_reinforcement_attempts)
                continue
            status, latest = self._idea_status(rows)
            enhancement_attempts = sum(
                1
                for row in rows
                if str(row.get("student_role") or "") == "enhancer"
                and str(row.get("role_mode") or "") == "epd_enhancement"
            )
            budget = int(idea.get("max_reinforcement_attempts") or self.max_reinforcement_attempts)
            # A failed enhancement is negative feedback about the original
            # promising idea, not a terminal overwrite after the first miss.
            if (
                enhancement_attempts >= budget
                and "validated" not in {str(row.get("epd_status") or "invalid") for row in rows}
            ):
                status = "invalid"
                idea["terminal_reason"] = "reinforcement_budget_exhausted"
            elif enhancement_attempts and status == "invalid":
                status = "promising"
            elif status != "invalid":
                idea["terminal_reason"] = ""
            idea.update(
                {
                    "status": status,
                    "executed": True,
                    "execution_count": len(rows),
                    "reinforcement_attempts": enhancement_attempts,
                    "max_reinforcement_attempts": budget,
                    "attempt_ids": [str(row.get("record_id") or "") for row in rows],
                    "updated_at": now,
                }
            )

    @staticmethod
    def _idea_status(rows: Sequence[Mapping[str, object]]) -> tuple[str, Mapping[str, object]]:
        latest = max(rows, key=lambda row: (int(row.get("round_index") or 0), int(row.get("updated_at") or 0)))
        statuses = {str(row.get("epd_status") or "invalid") for row in rows}
        if "validated" in statuses:
            return "validated", latest
        if "promising" in statuses:
            return "promising", latest
        if "pending" in statuses:
            return "pending", latest
        if "unactivated" in statuses:
            return "unactivated", latest
        return "invalid", latest

    def _descendant_stats(self) -> dict[str, dict[str, object]]:
        stats: dict[str, dict[str, object]] = {}
        for child in self.records():
            status = str(child.get("epd_status") or "invalid")
            gain = float(child.get("distance_gain") or 0.0)
            for parent_id in list(child.get("epd_record_ids") or ()):
                parent_id = str(parent_id)
                if not parent_id:
                    continue
                row = stats.setdefault(
                    parent_id,
                    {
                        "offspring_count": 0,
                        "offspring_validated_count": 0,
                        "offspring_invalid_count": 0,
                        "best_offspring_distance_gain": 0.0,
                    },
                )
                row["offspring_count"] = int(row["offspring_count"]) + 1
                if status == "validated":
                    row["offspring_validated_count"] = int(row["offspring_validated_count"]) + 1
                    row["best_offspring_distance_gain"] = max(float(row["best_offspring_distance_gain"]), gain)
                elif status == "invalid":
                    row["offspring_invalid_count"] = int(row["offspring_invalid_count"]) + 1
        return stats

    @staticmethod
    def _compact_attempt(row: Mapping[str, object]) -> dict[str, object]:
        artifacts = dict(row.get("artifacts") or {})
        return {
            "record_id": row.get("record_id"),
            "idea_id": row.get("idea_id"),
            "round_index": row.get("round_index"),
            "hypothesis_id": row.get("hypothesis_id"),
            "mechanism_family": row.get("mechanism_family"),
            "epd_status": row.get("epd_status"),
            "evidence_state": row.get("evidence_state"),
            "metrics": row.get("metrics"),
            "phase_signals": row.get("phase_signals") or {},
            "goal_distance": row.get("goal_distance"),
            "distance_gain": row.get("distance_gain"),
            "implementation_diff_artifact": artifacts.get("implementation_diff"),
            "source_change_bundle": row.get("source_change_bundle") or {},
        }

    @staticmethod
    def _source_change_bundle(diff: str, hypothesis: Mapping[str, object]) -> dict[str, object]:
        modified = tuple(dict.fromkeys(re.findall(r"^\+\+\+ b/(.+)$", diff, flags=re.MULTILINE)))
        added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
        removed = [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]
        # Store concise, replayable change facts. The full diff artifact stays
        # authoritative, while these excerpts make the Enhancer prompt usable.
        added_code = [line for line in added if line.strip()][:24]
        removed_code = [line for line in removed if line.strip()][:24]
        added_mechanism_changes = [
            f"added:{line.strip()}"
            for line in added_code
            if re.search(r"\b(if|for|while|return|commit|rollback|guard|policy|candidate)\b", line, re.IGNORECASE)
        ][:12]
        removed_mechanism_changes = [
            f"removed:{line.strip()}"
            for line in removed_code
            if re.search(r"\b(if|for|while|return|commit|rollback|guard|policy|candidate)\b", line, re.IGNORECASE)
        ][:12]
        telemetry_changes = [
            f"added:{line.strip()}"
            for line in added_code
            if "METRIC|" in line
        ][:12]
        return {
            "modified_files": list(modified),
            "added_code": added_code,
            "removed_code": removed_code,
            "added_mechanism_changes": added_mechanism_changes,
            "removed_mechanism_changes": removed_mechanism_changes,
            # Retain this aggregate key for prompt consumers created before
            # v2, while exposing the direction-specific fields above.
            "mechanism_changes": [*added_mechanism_changes, *removed_mechanism_changes],
            "telemetry_changes": telemetry_changes,
            "prior_claim": str(hypothesis.get("claim") or ""),
            "prior_predicted_stage_effect": str(hypothesis.get("teacher_predicted_stage_effect") or ""),
            "prior_source_hooks": [str(path) for path in list(hypothesis.get("source_hooks") or ()) if path],
        }

    def _persist_candidate_artifacts(self, *, record_id: str, candidate: CandidateResult) -> dict[str, str]:
        artifacts = {str(key): str(value) for key, value in candidate.artifacts.items()}
        root = self.path.parent / "epd_artifacts" / record_id
        root.mkdir(parents=True, exist_ok=True)
        diff_path = root / "implementation.diff"
        diff_path.write_text(candidate.implementation_diff, encoding="utf-8")
        hypothesis_path = root / "hypothesis.json"
        atomic_json(hypothesis_path, candidate.hypothesis.to_dict())
        candidate_path = root / "candidate.json"
        atomic_json(candidate_path, candidate.to_dict())
        artifacts.update(
            {
                "implementation_diff": str(diff_path),
                "hypothesis_artifact": str(hypothesis_path),
                "candidate_artifact": str(candidate_path),
            }
        )
        return artifacts

    @staticmethod
    def _recover_diff_artifact(artifacts: Mapping[str, object]) -> str:
        source = Path(str(artifacts.get("candidate_source") or ""))
        if not source.is_dir():
            return ""
        candidate_diff = source.parent.parent / "artifacts" / "implementation.diff"
        return str(candidate_diff) if candidate_diff.is_file() else ""

    @staticmethod
    def _checkpoint_metrics(value: str | None) -> dict[str, dict[str, float]]:
        if not value:
            return {}
        payload = load_json(Path(value), {}) or {}
        raw = payload.get("checkpoints") if isinstance(payload, dict) else {}
        return {
            str(stage): {
                str(name): float(metric)
                for name, metric in dict(metrics).items()
                if isinstance(metric, (int, float))
            }
            for stage, metrics in dict(raw or {}).items()
            if isinstance(metrics, dict)
        }
