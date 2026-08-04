from __future__ import annotations

import json
import shutil
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .codex_runtime import CodexRuntimeConfig, PersistentCodexRunner
from ..core.io import atomic_json
from ..core.models import CandidateResult, Hypothesis, Parent


@dataclass(frozen=True)
class CodexStudentConfig:
    model: str = "gpt-5.6-sol"
    reasoning_effort: str = "xhigh"
    retries: int = 3
    timeout_s: int = 3600
    # An engineering failure remains with its author; four bounded turns cover
    # diagnosis, repair, and a follow-up integration mistake without silently
    # deferring a broken mechanism to the next evolutionary round.
    max_repair_attempts: int = 4
    seed_home: Path = Path("outputs/codex_home")
    credential_env: Path | None = None
    allowed_patch_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class StudentEditReport:
    ok: bool
    detail: str
    operation_id: str
    thread_id: str | None
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class StudentReflectionReport:
    """Observer-only, evidence-grounded reflection from the editing Student."""

    ok: bool
    detail: str
    operation_id: str
    thread_id: str | None
    artifacts: dict[str, str]
    reflection: str
    recommended_lifecycle: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class NoopStudentEditor:
    """Explicit editor for deterministic controller tests; it never edits source."""

    name = "noop_student"

    def apply(self, **_: object) -> StudentEditReport:
        return StudentEditReport(True, "no_edit_requested", "noop", None, {})

    def repair(self, **_: object) -> StudentEditReport:
        return StudentEditReport(False, "noop_editor_cannot_repair", "noop_repair", None, {})

    def reflect(self, **_: object) -> StudentReflectionReport:
        return StudentReflectionReport(
            True,
            "noop_reflection",
            "noop_reflection",
            None,
            {},
            "No active Student reflection was available because this deterministic editor does not execute source changes.",
            None,
        )


class CodexStudentEditor:
    """Implement a bounded source hypothesis through one persistent Codex Student."""

    name = "codex_student"

    def __init__(self, config: CodexStudentConfig) -> None:
        self.config = config
        self.runner = PersistentCodexRunner(CodexRuntimeConfig(model=config.model, reasoning_effort=config.reasoning_effort, retries=config.retries, timeout_s=config.timeout_s, seed_home=config.seed_home, credential_env=config.credential_env))

    def apply(self, *, state_root: Path, round_index: int, student_id: str, workspace: Path, parent: Parent, hypothesis: Hypothesis, prompt_path: Path) -> StudentEditReport:
        source = workspace / "source"
        prompt = self._execution_prompt(prompt_path=prompt_path, source=source, parent=parent, hypothesis=hypothesis)
        # A source edit is fully specified by its round packet.  Starting a
        # fresh remote thread for the next packet prevents old source/tool
        # transcripts from dominating token use; repairs deliberately keep
        # this identity and therefore resume this exact edit conversation.
        identity = self._round_identity(student_id, round_index)
        turn = self.runner.run(state_root=state_root, identity=identity, operation_id=f"r{round_index:03d}_{student_id}", cwd=source, artifact_root=workspace.parent / "artifacts" / "codex", prompt=prompt)
        return self._report_after_turn(turn, workspace)

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
    ) -> StudentEditReport:
        """Repair a bounded evaluation/evidence gap in the same Student thread.

        This deliberately does not create a new Student or a new hypothesis: a
        compile/flow failure is evidence about an implementation, not a reason
        to discard the mechanism before that Student has had a bounded chance
        to make it executable.
        """
        source = workspace / "source"
        prompt = self._repair_prompt(
            prompt_path=prompt_path,
            source=source,
            parent=parent,
            hypothesis=hypothesis,
            failure_context=failure_context,
            repair_attempt=repair_attempt,
            repair_kind=repair_kind,
        )
        turn = self.runner.run(
            state_root=state_root,
            identity=self._round_identity(student_id, round_index),
            operation_id=f"r{round_index:03d}_{student_id}_{repair_kind}_repair_{repair_attempt:02d}",
            cwd=source,
            artifact_root=workspace.parent / "artifacts" / "codex" / f"{repair_kind}_repair_{repair_attempt:02d}",
            prompt=prompt,
        )
        return self._report_after_turn(turn, workspace)

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
    ) -> StudentReflectionReport:
        """Ask the same Student to observe final evidence without editing source."""
        source = workspace / "source"
        turn = self.runner.run(
            state_root=state_root,
            identity=self._round_identity(student_id, round_index),
            operation_id=f"r{round_index:03d}_{student_id}_reflection",
            cwd=source,
            artifact_root=workspace.parent / "artifacts" / "codex" / "reflection",
            prompt=self._reflection_prompt(
                prompt_path=prompt_path,
                source=source,
                parent=parent,
                hypothesis=hypothesis,
                candidate=candidate,
            ),
        )
        reflection, recommendation = self._parse_reflection(
            Path(str(turn.artifacts.get("codex_last_message") or ""))
        )
        return StudentReflectionReport(
            turn.ok,
            turn.detail,
            turn.operation_id,
            turn.thread_id,
            dict(turn.artifacts),
            reflection,
            recommendation,
        )

    @staticmethod
    def _round_identity(student_id: str, round_index: int) -> str:
        """Isolate remote context per evolutionary round, not per repair."""
        return f"{student_id}_r{round_index:03d}"

    @staticmethod
    def _report_after_turn(turn, workspace: Path) -> StudentEditReport:
        """Remove Student-created VCS state before the controller evaluates source.

        Candidate identity is deliberately the controller's normalized source
        hash/diff, not a Student-created commit.  Some Codex workflows create
        either ``source/.git`` or a sibling ``source.git`` even when instructed
        not to; both can alter CMake's generated version header or retain
        irrelevant mutable state between edit and evaluation.
        """
        removed = CodexStudentEditor._remove_student_vcs(workspace)
        artifacts = dict(turn.artifacts)
        if removed:
            artifacts["student_vcs_cleanup"] = ",".join(removed)
        return StudentEditReport(turn.ok, turn.detail, turn.operation_id, turn.thread_id, artifacts)

    @staticmethod
    def _remove_student_vcs(workspace: Path) -> tuple[str, ...]:
        removed: list[str] = []
        for path in (workspace / "source" / ".git", workspace / "source.git"):
            if not path.exists() and not path.is_symlink():
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(path.name if path.parent == workspace else "source/.git")
        return tuple(removed)

    @staticmethod
    def _write_internal_cpp_scheduling_decision(
        *, artifact_root: Path, last_message: Path, suggestion: str
    ) -> Path:
        """Persist the Student's advisory scheduling decision for later audit."""
        try:
            message = last_message.read_text(encoding="utf-8")
        except OSError:
            message = ""
        decision_match = re.search(
            r"(?im)^\s*-\s*Decision:\s*(accepted|adapted|rejected)\s*$", message
        )
        rationale_match = re.search(r"(?im)^\s*-\s*Rationale:\s*(.+?)\s*$", message)
        path = artifact_root / "internal_cpp_scheduling_decision.json"
        atomic_json(
            path,
            {
                "suggestion": suggestion,
                "decision": decision_match.group(1).lower() if decision_match else "not_recorded",
                "rationale": rationale_match.group(1).strip() if rationale_match else "not_recorded",
                "advisory_only": True,
            },
        )
        return path

    def _execution_prompt(self, *, prompt_path: Path, source: Path, parent: Parent, hypothesis: Hypothesis) -> str:
        allowed = ", ".join(self.config.allowed_patch_roots) or "only the assigned source hooks"
        exact = ", ".join(hypothesis.allowed_patch_paths) or "<no stage-specific restriction>"
        return "\n".join([prompt_path.read_text(encoding="utf-8"), "", "## Execution Contract — act now", f"You are the editing Student. Work only in this private source tree: `{source}`.", f"Common parent: `{parent.parent_id}`. Do not inspect or modify another Student or the seed tree.", f"Allowed source roots: `{allowed}`.", f"Primary edit hooks: `{', '.join(hypothesis.source_hooks)}`. These are source-grounded starting points, not a claim that the mechanism is confined to one file.", f"Exact stage patch boundary: `{exact}`. Any change outside this boundary is rejected before build. For a repair_power power-reclaim packet, the permitted subsystem is the complete repair_power call chain inside the allowed source root: command dispatch, REPAIR_POWER optimizer/configuration, dedicated power policies, and their helpers. If the desired behavior presently exists only in repair_timing (Setup/VT/size/buffer/parasitics/routing machinery), create or adapt a dedicated REPAIR_POWER counterpart and call it only through repair_power; do not change ordinary repair_timing semantics. Do not edit unrelated policy files.", "Implement one small, coherent C++ policy-level change that tests the assigned hypothesis. Do not only describe a patch: edit the files now.", "Keep investigation information-dense: locate symbols with rg first, then read only local ranges (at most 200 lines per command). Do not dump full files, source trees, unrelated project instructions, or unrelated skills into the model context. Inspect only declaration/call companions required to make the assigned edit compile.", "Do not reformat an entire file. Use a local apply_patch for the smallest semantic delta. Never run clang-format, a formatter, or a bulk style rewrite: preserve the exact surrounding layout manually and revert incidental formatting churn immediately rather than iterating on formatter options. A source diff must make the policy change reviewable.", "Do not modify benchmark data, evaluator code, checker code, build artifacts, or project orchestration. Do not run git init, create commits, or otherwise create repository metadata (including a sibling source.git): the controller computes the authoritative source diff and hash.", "When the Teacher Handoff contains an Internal C++ Scheduling Suggestion, independently inspect the current source and state `accepted`, `adapted`, or `rejected` with a concise source-grounded reason in your final response. It is advisory only: it may guide an internal C++ phase/policy schedule within your assigned source boundary, but it never authorizes a Tcl, SDC, design, benchmark, or Controller recipe change.", "Add or preserve a runtime telemetry line in the modified mechanism using this exact form when the mechanism fires: METRIC|<expected_signal>|<nonzero-number>. Use an expected signal from the packet and existing OpenROAD logging conventions.", "Do not run a full build or contest flow; the controller will do that after your turn. Before finishing, inspect a diff (without creating repository metadata) and summarize the precise source change, internal scheduling decision when applicable, and expected falsification condition."])

    def _repair_prompt(
        self,
        *,
        prompt_path: Path,
        source: Path,
        parent: Parent,
        hypothesis: Hypothesis,
        failure_context: str,
        repair_attempt: int,
        repair_kind: str = "engineering",
    ) -> str:
        allowed = ", ".join(self.config.allowed_patch_roots) or "only the assigned source hooks"
        exact = ", ".join(hypothesis.allowed_patch_paths) or "<no exact boundary recorded>"
        return "\n".join(
            [
                prompt_path.read_text(encoding="utf-8"),
                "",
                f"## {repair_kind.replace('_', ' ').title()} repair {repair_attempt}/{self.config.max_repair_attempts} — act now",
                f"You are the same persistent Student for parent `{parent.parent_id}` in private tree `{source}`.",
                f"Allowed source roots: `{allowed}`. Primary hooks: `{', '.join(hypothesis.source_hooks)}`.",
                f"Exact repair patch boundary: `{exact}`. Any change outside this boundary is rejected before evidence is recorded.",
                "The controller evaluated your current edit and found the gap described below. Repair the C++ implementation now, preserving the assigned mechanism and telemetry intent. Do not switch hypotheses, edit the evaluator/checker/benchmark/build artifacts, create Git metadata/commits (including a sibling source.git), or merely explain the error.",
                "For a telemetry repair, an expected signal proves only the exact event named by that signal. Never satisfy it by relabeling or arithmetically deriving counts from an earlier phase, predecessor tranche, disabled branch, rejected alternative, or unrelated loop. If the named mechanism genuinely did not execute, preserve that truthful nonactivation instead of manufacturing a nonzero metric.",
                "Inspect the cited log files and current diff, make the smallest coherent fix, then inspect the resulting diff. Keep tool output information-dense: search with rg first and read local ranges of at most 200 lines; never dump whole files, trees, unrelated instructions, or unrelated skills. Do not run clang-format or any formatter, reformat unrelated source lines, or create whitespace-only churn. Treat a build error as a whole-source integration failure: before responding, search every allowed source file and its message/declaration companions for the conflicting symbol, ID, signature, or registration (for example with rg). Do not change only the first occurrence named by the compiler if another occurrence can still conflict. Do not run the full contest flow; the controller will immediately rebuild and reevaluate after this turn.",
                "## Controller failure context (authoritative)",
                failure_context,
            ]
        )

    @staticmethod
    def _parse_reflection(path: Path) -> tuple[str, str | None]:
        """Accept only the documented lifecycle line; prose has no authority."""
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return "Student reflection transcript was unavailable.", None
        matches = re.findall(
            r"(?im)^\s*Recommended EPD Lifecycle:\s*(validated|promising|invalid|unactivated)\s*$",
            raw,
        )
        reflection = re.sub(
            r"(?im)^\s*Recommended EPD Lifecycle:\s*.*$",
            "",
            raw,
        ).strip()
        # Persist a single compact paragraph instead of model formatting
        # noise. A malformed/missing lifecycle line remains non-authoritative.
        reflection = " ".join(reflection.split()) or "Student reflection transcript was empty."
        return reflection, matches[0] if len(matches) == 1 else None

    def _reflection_prompt(
        self,
        *,
        prompt_path: Path,
        source: Path,
        parent: Parent,
        hypothesis: Hypothesis,
        candidate: CandidateResult,
    ) -> str:
        checks = {item.name: item.passed for item in candidate.checks}
        evidence = {
            "parent_id": parent.parent_id,
            "hypothesis_id": hypothesis.hypothesis_id,
            "metrics": candidate.metrics,
            "phase_signals": candidate.phase_signals,
            "official_checks": checks,
            "evaluation_error": candidate.evaluation_error,
            "evidence_artifacts": {
                key: value
                for key, value in candidate.artifacts.items()
                if key in {"evaluation_log", "checkpoint_metrics", "official_4of4_log", "implementation_diff"}
            },
        }
        return "\n".join(
            [
                prompt_path.read_text(encoding="utf-8"),
                "",
                "## Post-evaluation Student Reflection — observe only",
                f"You are the same persistent Student for parent `{parent.parent_id}` in `{source}`.",
                "Do not edit files, run builds or flows, change Tcl/recipes, create Git metadata, propose a patch, or decide promotion. The Controller alone owns official 4/4 evidence, lifecycle status, and promotion.",
                "Using only the final evidence below, output exactly one evidence-grounded paragraph covering intended mechanism, actual implementation, activation, stage/final QoR effect, failure attribution, reusable lesson, avoid-next-time mechanism, limitation, and bounded next refinement. Then output exactly one separate line: `Recommended EPD Lifecycle: <validated|promising|invalid|unactivated>`. This is a nonbinding recommendation; do not add other fields or headings.",
                "## Controller evidence (authoritative)",
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            ]
        )
