from __future__ import annotations

import hashlib
import json
import shutil
import re
import subprocess
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
        recovered = self._recover_exact_seed_reference_patch(
            workspace=workspace,
            parent=parent,
            hypothesis=hypothesis,
        )
        if recovered is not None:
            return recovered
        materialized = self._materialize_exact_seed_reference_patch(
            workspace=workspace,
            parent=parent,
            hypothesis=hypothesis,
        )
        if materialized is not None:
            return materialized
        exact_seed_id = self._exact_seed_materialization_id(hypothesis)
        if exact_seed_id:
            return StudentEditReport(
                False,
                f"exact_seed_reference_patch_not_applicable:{exact_seed_id}",
                f"seed_reference_patch_materialization:{exact_seed_id}",
                None,
                {},
            )
        prompt = self._execution_prompt(prompt_path=prompt_path, source=source, parent=parent, hypothesis=hypothesis)
        # A source edit is fully specified by its round packet.  Starting a
        # fresh remote thread for the next packet prevents old source/tool
        # transcripts from dominating token use; repairs deliberately keep
        # this identity and therefore resume this exact edit conversation.
        identity = self._round_identity(student_id, round_index)
        turn = self.runner.run(state_root=state_root, identity=identity, operation_id=f"r{round_index:03d}_{student_id}", cwd=source, artifact_root=workspace.parent / "artifacts" / "codex", prompt=prompt)
        report = self._report_after_turn(turn, workspace)
        if report.ok:
            return report
        replay = self._replay_exact_seed_reference_patch(
            workspace=workspace,
            parent=parent,
            hypothesis=hypothesis,
            failed_report=report,
        )
        return replay or report

    def _recover_exact_seed_reference_patch(
        self,
        *,
        workspace: Path,
        parent: Parent,
        hypothesis: Hypothesis,
    ) -> StudentEditReport | None:
        """Reuse one fully audited seed replay after an interrupted round."""
        if (
            hypothesis.student_role != "explorer"
            or hypothesis.role_mode != "seed_revalidation"
            or len(hypothesis.candidate_options) != 1
        ):
            return None
        option = dict(hypothesis.candidate_options[0])
        seed_id = str(option.get("candidate_id") or option.get("seed_id") or "").strip()
        references = tuple(
            Path(str(path)).resolve()
            for path in list(option.get("reference_diff_paths") or ())
            if str(path).strip()
        )
        materialization_mode = str(option.get("materialization_mode") or "").strip()
        artifact_name = (
            "seed_reference_patch_materialization.json"
            if materialization_mode == "exact_reference_patch"
            else "seed_reference_patch_replay.json"
        )
        replay_artifact = workspace.parent / "artifacts" / artifact_name
        try:
            replay = json.loads(replay_artifact.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(replay, dict) or len(references) != 1:
            return None
        reference = references[0]
        patch_paths = self._reference_patch_paths(reference)
        reference_parent_hashes = self._reference_parent_file_hashes(
            option, patch_paths
        )
        if (
            not seed_id
            or not (workspace / "source").is_dir()
            or not patch_paths
            or not self._paths_within_allowed_roots(patch_paths)
            or (
                materialization_mode == "exact_reference_patch"
                and reference_parent_hashes is None
            )
            or str(replay.get("schema_version") or "")
            != (
                "goalevolve.v2.seed-reference-patch-materialization.v1"
                if materialization_mode == "exact_reference_patch"
                else "goalevolve.v2.seed-reference-patch-replay.v1"
            )
            or str(replay.get("authority") or "")
            != (
                "controller_authorized_single_exact_seed_materialization"
                if materialization_mode == "exact_reference_patch"
                else "external_codex_failure_single_exact_seed_revalidation"
            )
            or str(replay.get("seed_id") or "") != seed_id
            or str(replay.get("parent_id") or "") != parent.parent_id
            or str(replay.get("parent_source_hash") or "") != parent.source_hash
            or Path(str(replay.get("reference_diff") or "")).resolve() != reference
            or tuple(str(path) for path in list(replay.get("patch_paths") or ()))
            != patch_paths
            or (
                materialization_mode == "exact_reference_patch"
                and dict(replay.get("reference_parent_file_hashes") or {})
                != reference_parent_hashes
            )
            or dict(replay.get("post_apply_hashes") or {})
            != self._source_file_hashes(workspace / "source", patch_paths)
        ):
            return None
        return StudentEditReport(
            True,
            f"seed_reference_patch_recovered:{seed_id}",
            f"seed_reference_patch_recovery:{seed_id}",
            None,
            {
                "seed_reference_patch_materialization": str(replay_artifact)
                if materialization_mode == "exact_reference_patch"
                else "",
                "seed_reference_patch_fallback": str(replay_artifact)
                if materialization_mode != "exact_reference_patch"
                else "",
                "seed_reference_patch_diff": str(reference),
            },
        )

    @staticmethod
    def _exact_seed_materialization_id(hypothesis: Hypothesis) -> str:
        """Return the seed ID only for the Controller's explicit opt-in mode."""
        if (
            hypothesis.student_role != "explorer"
            or hypothesis.role_mode != "seed_revalidation"
            or len(hypothesis.candidate_options) != 1
        ):
            return ""
        option = dict(hypothesis.candidate_options[0])
        if str(option.get("materialization_mode") or "").strip() != "exact_reference_patch":
            return ""
        return str(option.get("candidate_id") or option.get("seed_id") or "").strip()

    def _materialize_exact_seed_reference_patch(
        self,
        *,
        workspace: Path,
        parent: Parent,
        hypothesis: Hypothesis,
    ) -> StudentEditReport | None:
        """Apply a Controller-authorized historical source transform exactly once.

        This is deliberately separate from transport-failure replay: opting in
        proves that the Controller selected a complete source transform, never
        that its historical QoR is valid.  The normal evaluator must still
        produce new build, official-flow, LEC, and activation evidence.
        """
        seed_id = self._exact_seed_materialization_id(hypothesis)
        if not seed_id:
            return None
        option = dict(hypothesis.candidate_options[0])
        references = tuple(
            Path(str(path)).resolve()
            for path in list(option.get("reference_diff_paths") or ())
            if str(path).strip()
        )
        if len(references) != 1:
            return None
        reference = references[0]
        if reference.suffix != ".diff" or not reference.is_file():
            return None
        source = workspace / "source"
        manifest_path = workspace.parent / "workspace_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        parent_source = Path(str(manifest.get("parent_source") or ""))
        if (
            not source.is_dir()
            or str(manifest.get("parent_id") or "") != parent.parent_id
            or str(manifest.get("parent_source_hash") or "") != parent.source_hash
            or not parent_source.is_dir()
        ):
            return None
        patch_paths = self._reference_patch_paths(reference)
        reference_parent_hashes = self._reference_parent_file_hashes(
            option, patch_paths
        )
        if (
            not patch_paths
            or reference_parent_hashes is None
            or not self._paths_within_allowed_roots(patch_paths)
            or self._source_file_hashes(source, patch_paths) != reference_parent_hashes
            or self._source_file_hashes(parent_source, patch_paths)
            != reference_parent_hashes
        ):
            return None
        dry_run = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "--dry-run", "-p1", "-d", str(source), "-i", str(reference)],
            text=True,
            capture_output=True,
            check=False,
        )
        if dry_run.returncode != 0:
            return None
        applied = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-d", str(source), "-i", str(reference)],
            text=True,
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            return None
        post_apply_hashes = self._source_file_hashes(source, patch_paths)
        if len(post_apply_hashes) != len(patch_paths):
            return None
        artifact_root = workspace.parent / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        artifact = artifact_root / "seed_reference_patch_materialization.json"
        atomic_json(
            artifact,
            {
                "schema_version": "goalevolve.v2.seed-reference-patch-materialization.v1",
                "seed_id": seed_id,
                "parent_id": parent.parent_id,
                "parent_source_hash": parent.source_hash,
                "reference_diff": str(reference),
                "patch_paths": list(patch_paths),
                "reference_parent_file_hashes": reference_parent_hashes,
                "post_apply_hashes": post_apply_hashes,
                "authority": "controller_authorized_single_exact_seed_materialization",
                "dry_run_stdout": dry_run.stdout,
                "dry_run_stderr": dry_run.stderr,
                "apply_stdout": applied.stdout,
                "apply_stderr": applied.stderr,
            },
        )
        return StudentEditReport(
            True,
            f"seed_reference_patch_materialized:{seed_id}",
            f"seed_reference_patch_materialization:{seed_id}",
            None,
            {
                "seed_reference_patch_materialization": str(artifact),
                "seed_reference_patch_diff": str(reference),
            },
        )

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
        """Repair a bounded evaluation/evidence gap for the same Student role.

        This deliberately does not create a new Student or a new hypothesis: a
        compile/flow failure is evidence about an implementation, not a reason
        to discard the mechanism before that Student has had a bounded chance
        to make it executable.  The repair receives a complete current-source
        and failure-evidence packet, so it starts a fresh remote session rather
        than resuming unbounded source/tool transcripts from the edit turn.
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
            identity=self._repair_identity(
                student_id, round_index, repair_kind, repair_attempt
            ),
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
        """Isolate initial editing context per evolutionary round."""
        return f"{student_id}_r{round_index:03d}"

    @staticmethod
    def _repair_identity(
        student_id: str, round_index: int, repair_kind: str, repair_attempt: int
    ) -> str:
        """Bound one repair to its current source and Controller evidence."""
        normalized_kind = re.sub(r"[^a-z0-9]+", "_", repair_kind.lower()).strip("_")
        return (
            f"{CodexStudentEditor._round_identity(student_id, round_index)}_"
            f"{normalized_kind or 'engineering'}_repair_{repair_attempt:02d}"
        )

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

    def _replay_exact_seed_reference_patch(
        self,
        *,
        workspace: Path,
        parent: Parent,
        hypothesis: Hypothesis,
        failed_report: StudentEditReport,
    ) -> StudentEditReport | None:
        """Revalidate one controller-scheduled seed after a transport failure.

        Reference patches remain source patterns, not QoR evidence.  This
        intentionally narrow path is available only when Codex produced no
        edit because its transport failed, and then only for one scheduled
        seed whose patch applies to the current private parent with zero fuzz.
        The normal evaluator still builds and runs the fresh candidate.
        """
        if not str(failed_report.detail or "").lower().startswith("codex_failed:"):
            return None
        if (
            hypothesis.student_role != "explorer"
            or hypothesis.role_mode != "seed_revalidation"
            or len(hypothesis.candidate_options) != 1
        ):
            return None
        option = dict(hypothesis.candidate_options[0])
        seed_id = str(option.get("candidate_id") or option.get("seed_id") or "").strip()
        references = tuple(
            Path(str(path)).resolve()
            for path in list(option.get("reference_diff_paths") or ())
            if str(path).strip()
        )
        if not seed_id or len(references) != 1:
            return None
        reference = references[0]
        if reference.suffix != ".diff" or not reference.is_file():
            return None
        source = workspace / "source"
        manifest_path = workspace.parent / "workspace_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if (
            not source.is_dir()
            or str(manifest.get("parent_id") or "") != parent.parent_id
            or str(manifest.get("parent_source_hash") or "") != parent.source_hash
            or not Path(str(manifest.get("parent_source") or "")).is_dir()
        ):
            return None
        patch_paths = self._reference_patch_paths(reference)
        if not patch_paths or not self._paths_within_allowed_roots(patch_paths):
            return None
        dry_run = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "--dry-run", "-p1", "-d", str(source), "-i", str(reference)],
            text=True,
            capture_output=True,
            check=False,
        )
        if dry_run.returncode != 0:
            return None
        applied = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-d", str(source), "-i", str(reference)],
            text=True,
            capture_output=True,
            check=False,
        )
        if applied.returncode != 0:
            return None
        artifact_root = workspace.parent / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        replay_artifact = artifact_root / "seed_reference_patch_replay.json"
        post_apply_hashes = self._source_file_hashes(source, patch_paths)
        if len(post_apply_hashes) != len(patch_paths):
            return None
        atomic_json(
            replay_artifact,
            {
                "schema_version": "goalevolve.v2.seed-reference-patch-replay.v1",
                "seed_id": seed_id,
                "parent_id": parent.parent_id,
                "parent_source_hash": parent.source_hash,
                "reference_diff": str(reference),
                "patch_paths": list(patch_paths),
                "post_apply_hashes": post_apply_hashes,
                "authority": "external_codex_failure_single_exact_seed_revalidation",
                "dry_run_stdout": dry_run.stdout,
                "dry_run_stderr": dry_run.stderr,
                "apply_stdout": applied.stdout,
                "apply_stderr": applied.stderr,
            },
        )
        return StudentEditReport(
            True,
            f"seed_reference_patch_applied:{seed_id}",
            failed_report.operation_id,
            failed_report.thread_id,
            {
                **failed_report.artifacts,
                "seed_reference_patch_fallback": str(replay_artifact),
                "seed_reference_patch_diff": str(reference),
            },
        )

    @staticmethod
    def _reference_patch_paths(reference: Path) -> tuple[str, ...]:
        """Read relative targets before handing a historical diff to patch."""
        paths: list[str] = []
        try:
            lines = reference.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ()
        for line in lines:
            if not (line.startswith("--- ") or line.startswith("+++ ")):
                continue
            raw = line[4:].split("\t", 1)[0].strip()
            if raw == "/dev/null":
                return ()
            normalized = raw[2:] if raw.startswith(("a/", "b/")) else raw
            path = Path(normalized)
            if not normalized or path.is_absolute() or ".." in path.parts:
                return ()
            paths.append(path.as_posix())
        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _reference_parent_file_hashes(
        option: dict[str, object], patch_paths: tuple[str, ...]
    ) -> dict[str, str] | None:
        """Accept only a complete SHA-256 map for an exact reference base."""
        raw = option.get("reference_parent_file_hashes")
        if not isinstance(raw, dict) or set(raw) != set(patch_paths):
            return None
        hashes = {str(path): str(value) for path, value in raw.items()}
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in hashes.values()
        ):
            return None
        return hashes

    def _paths_within_allowed_roots(self, paths: tuple[str, ...]) -> bool:
        roots = tuple(Path(root) for root in self.config.allowed_patch_roots if root)
        if not roots:
            return False
        return all(
            any(path == root or root in Path(path).parents for root in roots)
            for path in map(Path, paths)
        )

    @staticmethod
    def _source_file_hashes(source: Path, paths: tuple[str, ...]) -> dict[str, str]:
        """Hash each replay target to make an interrupted replay resumable."""
        hashes: dict[str, str] = {}
        for relative_path in paths:
            target = source / relative_path
            try:
                if not target.is_file():
                    return {}
                hashes[relative_path] = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError:
                return {}
        return hashes

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
        evidence = self._reflection_controller_evidence(
            candidate=candidate,
            parent=parent,
            hypothesis=hypothesis,
        )
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

    @staticmethod
    def _reflection_controller_evidence(
        *,
        candidate: CandidateResult,
        parent: Parent,
        hypothesis: Hypothesis,
    ) -> dict[str, object]:
        """Load the immutable post-verdict packet for observer-only review.

        Direct unit callers may not have reached Controller classification yet;
        retain a compact explicit fallback for them rather than silently
        claiming a matched comparison or final verdict exists.
        """
        path = Path(
            str(candidate.artifacts.get("student_reflection_controller_evidence") or "")
        )
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                return payload
        checks = {item.name: item.passed for item in candidate.checks}
        return {
            "schema_version": "goalevolve.v2.student-reflection-evidence.v1",
            "authority": "controller_observation_only",
            "lineage_parent": parent.to_dict(),
            "comparison_parent": {"status": "not_available_before_controller_classification"},
            "decision_context": {},
            "candidate": {
                "hypothesis_id": hypothesis.hypothesis_id,
                "metrics": candidate.metrics,
                "phase_signals": candidate.phase_signals,
                "official_checks": checks,
                "evaluation_error": candidate.evaluation_error,
                "evidence_artifacts": {
                    key: value
                    for key, value in candidate.artifacts.items()
                    if key in {
                        "evaluation_log",
                        "checkpoint_metrics",
                        "official_4of4_log",
                        "implementation_diff",
                    }
                },
            },
            "final_verdict": {"status": "not_available_before_controller_classification"},
        }
