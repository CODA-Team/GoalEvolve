from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

from .contracts import GoalContract
from .models import CandidateResult, EvidenceVerdict, Hypothesis, Parent


class Planner(Protocol):
    name: str

    def plan(
        self,
        *,
        contract: GoalContract,
        parent: Parent,
        round_index: int,
        student_ids: Sequence[str],
        state_root: Path,
        diagnosis=None,
        decision_context: dict[str, object] | None = None,
    ) -> list[Hypothesis]: ...


class Evaluator(Protocol):
    name: str

    def evaluate(
        self,
        *,
        contract: GoalContract,
        parent: Parent,
        hypothesis: Hypothesis,
        student_id: str,
        workspace: Path,
        round_index: int,
    ) -> CandidateResult: ...


class StudentEditor(Protocol):
    name: str

    def apply(
        self,
        *,
        state_root: Path,
        round_index: int,
        student_id: str,
        workspace: Path,
        parent: Parent,
        hypothesis: Hypothesis,
        prompt_path: Path,
    ): ...

    def repair(
        self,
        *,
        state_root: Path,
        round_index: int,
        student_id: str,
        workspace: Path,
        parent: Parent,
        hypothesis: Hypothesis,
        prompt_path: Path,
        failure_context: str,
        repair_attempt: int,
        repair_kind: str = "engineering",
    ): ...

    def reflect(
        self,
        *,
        state_root: Path,
        round_index: int,
        student_id: str,
        workspace: Path,
        parent: Parent,
        hypothesis: Hypothesis,
        prompt_path: Path,
        candidate: CandidateResult,
    ): ...


class Teacher(Protocol):
    name: str

    def plan(self, **kwargs): ...

    def review(self, **kwargs): ...


class WorkspaceProvider(Protocol):
    name: str

    def prepare(self, *, state_root: Path, round_index: int, student_id: str, parent: Parent) -> Path: ...


class PromotionPolicy(Protocol):
    name: str

    def classify(self, *, contract: GoalContract, parent: Parent, candidate: CandidateResult) -> EvidenceVerdict: ...

    def choose(self, rows: Sequence[tuple[CandidateResult, EvidenceVerdict]]) -> tuple[CandidateResult, EvidenceVerdict] | None: ...


class PluginRegistry:
    """Small explicit registry: experiments choose components by name, no hidden globals."""

    def __init__(self) -> None:
        self._planners: dict[str, Planner] = {}
        self._evaluators: dict[str, Evaluator] = {}
        self._student_editors: dict[str, StudentEditor] = {}
        self._teachers: dict[str, Teacher] = {}
        self._workspaces: dict[str, WorkspaceProvider] = {}
        self._promotions: dict[str, PromotionPolicy] = {}

    def register_planner(self, plugin: Planner) -> None:
        self._register(self._planners, plugin)

    def register_evaluator(self, plugin: Evaluator) -> None:
        self._register(self._evaluators, plugin)

    def register_student_editor(self, plugin: StudentEditor) -> None:
        self._register(self._student_editors, plugin)

    def register_teacher(self, plugin: Teacher) -> None:
        self._register(self._teachers, plugin)

    def register_workspace(self, plugin: WorkspaceProvider) -> None:
        self._register(self._workspaces, plugin)

    def register_promotion(self, plugin: PromotionPolicy) -> None:
        self._register(self._promotions, plugin)

    @staticmethod
    def _register(store: dict[str, object], plugin: object) -> None:
        name = str(getattr(plugin, "name", "")).strip()
        if not name:
            raise ValueError("plugin needs a nonempty name")
        if name in store:
            raise ValueError(f"duplicate plugin: {name}")
        store[name] = plugin

    def planner(self, name: str) -> Planner:
        return self._planners[name]

    def evaluator(self, name: str) -> Evaluator:
        return self._evaluators[name]

    def student_editor(self, name: str) -> StudentEditor:
        return self._student_editors[name]

    def teacher(self, name: str) -> Teacher:
        return self._teachers[name]

    def workspace(self, name: str) -> WorkspaceProvider:
        return self._workspaces[name]

    def promotion(self, name: str) -> PromotionPolicy:
        return self._promotions[name]

    def manifest(self) -> dict[str, list[str]]:
        return {
            "planners": sorted(self._planners),
            "evaluators": sorted(self._evaluators),
            "student_editors": sorted(self._student_editors),
            "teachers": sorted(self._teachers),
            "workspaces": sorted(self._workspaces),
            "promotions": sorted(self._promotions),
        }
