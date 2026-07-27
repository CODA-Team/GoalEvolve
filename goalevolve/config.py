from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core.contracts import GoalContract, build_contract
from .testing.evaluators import MockEvaluator
from .core.plugins import PluginRegistry
from .planning.retrieval import DiversePlanner, RoundRobinPlanner
from .execution.workspace import IsolatedWorkspace
from .core.provenance import toolchain_fingerprint
from .evaluation.promotion import PowerFirstPromotion, StrictEvidencePromotion
from .execution.execution import ExecutionPolicy
from .planning.scope import SourceScopeResolver
from .evaluation.contest2026 import Contest2026Config, Contest2026OpenROADEvaluator
from .agents.codex_student import CodexStudentConfig, CodexStudentEditor, NoopStudentEditor
from .agents.teacher import CodexTeacher, CodexTeacherConfig, HeuristicTeacher


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODEX_CONFIG_PATH = PROJECT_ROOT / "config" / "codex.json"
# Public experiment profiles use these relocatable defaults.  A local machine
# may override either one without editing a reviewed JSON artifact.
DEFAULT_OPENROAD_SEED = Path(
    os.environ.get(
        "GOALEVOLVE_OPENROAD_SEED",
        PROJECT_ROOT / "artifact_evaluation" / "lineage" / "openroad_power" / "p0" / "source",
    )
)
DEFAULT_BENCHMARK_ROOT = Path(os.environ.get("GOALEVOLVE_BENCHMARK_ROOT", PROJECT_ROOT / "third_party" / "benchmarks" / "benchmarks"))
DEFAULT_CREDENTIAL_ENV = PROJECT_ROOT / "config" / "credentials" / "goalevolve_codex.env"


@dataclass(frozen=True)
class CodexWorkerSettings:
    model: str
    reasoning_effort: str
    retries: int
    timeout_s: int
    max_repair_attempts: int | None = None


@dataclass(frozen=True)
class CodexSettings:
    student: CodexWorkerSettings
    teacher: CodexWorkerSettings
    credential_env: Path


def load_codex_settings(path: Path = CODEX_CONFIG_PATH) -> CodexSettings:
    """Load one project-wide, versioned Codex runtime policy."""
    raw = json.loads(path.read_text(encoding="utf-8"))

    def worker(name: str, *, needs_repairs: bool) -> CodexWorkerSettings:
        values = dict(raw.get(name) or {})
        required = ("model", "reasoning_effort", "retries", "timeout_s")
        missing = [field for field in required if field not in values]
        if needs_repairs and "max_repair_attempts" not in values:
            missing.append("max_repair_attempts")
        if missing:
            raise ValueError(f"Codex configuration {name!r} lacks: {', '.join(missing)}")
        return CodexWorkerSettings(
            model=str(values["model"]),
            reasoning_effort=str(values["reasoning_effort"]),
            retries=int(values["retries"]),
            timeout_s=int(values["timeout_s"]),
            max_repair_attempts=int(values["max_repair_attempts"]) if needs_repairs else None,
        )

    credential_env = Path(str(raw.get("credential_env") or "credentials/goalevolve_codex.env"))
    return CodexSettings(
        student=worker("student", needs_repairs=True),
        teacher=worker("teacher", needs_repairs=False),
        credential_env=(path.parent / credential_env).resolve() if not credential_env.is_absolute() else credential_env,
    )


@dataclass(frozen=True)
class ExperimentConfig:
    design: str
    state_root: Path
    baseline_metrics: dict[str, float]
    target_metrics: dict[str, float]
    metric_weights: dict[str, float]
    hard_metrics: set[str]
    maximize_metrics: set[str]
    planner: str
    evaluator: str
    student_editor: str
    teacher: str
    workspace: str
    promotion: str
    students: tuple[str, ...]
    power_stage_tns_ceiling_ns: float = 30.0
    power_stage_protected_rounds: int = 10
    power_reclaim_phase: str = "early_forced_reclaim"
    power_reclaim_proportion_percent: float = 80.0
    power_reclaim_max_moves: int = 0
    allowed_patch_roots: tuple[str, ...] = ()
    require_cpp_patch: bool = True
    command_timeout_s: int = 7200
    command_retries: int = 1
    min_free_gb: float = 2.0
    source_root: Path | None = None
    build_seed_root: Path | None = None
    benchmark_root: Path = DEFAULT_BENCHMARK_ROOT
    baseline_evaluation_root: Path | None = None
    build_jobs: int = 2
    codex: CodexSettings = field(default_factory=load_codex_settings)
    initial_parent_id: str | None = None
    initial_parent_source_root: Path | None = None
    initial_parent_metrics: dict[str, float] | None = None
    initial_parent_source_commit: str | None = None
    initial_parent_source_hash: str | None = None
    initial_parent_evaluation_mode: str = "unknown"
    initial_parent_artifacts: dict[str, str] | None = None
    max_campaign_rounds: int | None = None
    prefer_execution_champion: bool = False
    campaign_ready: bool | None = None


def _load_raw_config(path: Path) -> dict[str, Any]:
    """Load one reviewed profile, optionally inheriting a sibling profile.

    Inheritance is deliberately shallow and explicit: an experiment profile
    may override top-level fields of a portable base profile, but it cannot
    import arbitrary Python or silently merge runtime state.
    """
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    parent_name = raw.pop("extends", None)
    if not parent_name:
        return raw
    parent_path = (path.parent / str(parent_name)).resolve()
    if not parent_path.is_file() or parent_path == path.resolve():
        raise ValueError(f"invalid experiment profile parent: {parent_name}")
    parent = _load_raw_config(parent_path)
    parent.update(raw)
    return parent


def load_config(path: Path) -> ExperimentConfig:
    path = path.resolve()
    raw = _load_raw_config(path)
    base = path.parent

    def optional_path(value: Any) -> Path | None:
        if not value:
            return None
        candidate = Path(str(value)).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    design = str(raw["design"])
    evaluator = str(raw.get("evaluator") or "mock")
    student_editor = str(raw.get("student_editor") or ("codex_student" if evaluator == "contest_openroad" else "noop_student"))
    teacher = str(raw.get("teacher") or ("codex_teacher" if evaluator == "contest_openroad" else "heuristic_teacher"))
    source_root = optional_path(raw.get("source_root"))
    if evaluator == "contest_openroad" and source_root is None:
        source_root = DEFAULT_OPENROAD_SEED
    return ExperimentConfig(
        design=design,
        state_root=optional_path(raw.get("state_root")) or (PROJECT_ROOT / "outputs" / "ae3" / design),
        baseline_metrics={key: float(value) for key, value in dict(raw["baseline_metrics"]).items()},
        target_metrics={key: float(value) for key, value in dict(raw["target_metrics"]).items()},
        metric_weights={key: float(value) for key, value in dict(raw.get("metric_weights") or {}).items()},
        hard_metrics=set(raw.get("hard_metrics") or []),
        maximize_metrics=set(raw.get("maximize_metrics") or []),
        planner=str(raw.get("planner") or "diverse_planner"),
        evaluator=evaluator,
        student_editor=student_editor,
        teacher=teacher,
        workspace=str(raw.get("workspace") or "isolated_workspace"),
        promotion=str(raw.get("promotion") or "strict_evidence"),
        power_stage_tns_ceiling_ns=float(raw.get("power_stage_tns_ceiling_ns", 30.0)),
        power_stage_protected_rounds=int(raw.get("power_stage_protected_rounds", 10)),
        power_reclaim_phase=str(raw.get("power_reclaim_phase") or "early_forced_reclaim"),
        power_reclaim_proportion_percent=float(raw.get("power_reclaim_proportion_percent", 80.0)),
        power_reclaim_max_moves=int(raw.get("power_reclaim_max_moves", 0)),
        students=tuple(raw.get("students") or ("student_1", "student_2", "student_3", "student_4")),
        allowed_patch_roots=tuple(raw.get("allowed_patch_roots") or ()),
        require_cpp_patch=bool(raw.get("require_cpp_patch", True)),
        command_timeout_s=int(raw.get("command_timeout_s", 7200)),
        command_retries=int(raw.get("command_retries", 1)),
        min_free_gb=float(raw.get("min_free_gb", 2.0)),
        source_root=source_root,
        build_seed_root=optional_path(raw.get("build_seed_root")),
        benchmark_root=optional_path(raw.get("benchmark_root")) or DEFAULT_BENCHMARK_ROOT,
        baseline_evaluation_root=optional_path(raw.get("baseline_evaluation_root")),
        build_jobs=int(raw.get("build_jobs", 2)),
        codex=load_codex_settings(),
        initial_parent_id=str(raw.get("initial_parent_id") or "") or None,
        initial_parent_source_root=optional_path(raw.get("initial_parent_source_root")),
        initial_parent_metrics={key: float(value) for key, value in dict(raw.get("initial_parent_metrics") or {}).items()} or None,
        initial_parent_source_commit=str(raw.get("initial_parent_source_commit") or "") or None,
        initial_parent_source_hash=str(raw.get("initial_parent_source_hash") or "") or None,
        initial_parent_evaluation_mode=str(raw.get("initial_parent_evaluation_mode") or "unknown"),
        initial_parent_artifacts={
            str(key): str(optional_path(value))
            for key, value in dict(raw.get("initial_parent_artifacts") or {}).items()
            if optional_path(value) is not None
        } or None,
        max_campaign_rounds=int(raw["max_campaign_rounds"]) if raw.get("max_campaign_rounds") is not None else None,
        prefer_execution_champion=bool(raw.get("prefer_execution_champion", False)),
        campaign_ready=bool(raw["campaign_ready"]) if "campaign_ready" in raw else None,
    )


def build_runtime(config: ExperimentConfig) -> tuple[GoalContract, PluginRegistry]:
    toolchain = toolchain_fingerprint(source_root=config.source_root)
    contract = build_contract(
        design=config.design,
        baseline_metrics=config.baseline_metrics,
        target_metrics=config.target_metrics,
        weights=config.metric_weights,
        hard_metrics=config.hard_metrics,
        maximize_metrics=config.maximize_metrics,
        source_fingerprint={key: str(value) for key, value in toolchain.items() if value},
    )
    registry = PluginRegistry()
    registry.register_planner(DiversePlanner(scope_resolver=SourceScopeResolver(config.source_root, include_roots=config.allowed_patch_roots)))
    registry.register_planner(RoundRobinPlanner())
    registry.register_workspace(IsolatedWorkspace(config.source_root))
    registry.register_promotion(StrictEvidencePromotion())
    registry.register_promotion(
        PowerFirstPromotion(
            power_stage_tns_ceiling_ns=config.power_stage_tns_ceiling_ns,
            protected_power_rounds=config.power_stage_protected_rounds,
        )
    )
    registry.register_evaluator(MockEvaluator())
    registry.register_student_editor(NoopStudentEditor())
    registry.register_teacher(HeuristicTeacher())
    registry.register_teacher(CodexTeacher(CodexTeacherConfig(model=config.codex.teacher.model, reasoning_effort=config.codex.teacher.reasoning_effort, retries=config.codex.teacher.retries, timeout_s=config.codex.teacher.timeout_s, seed_home=config.state_root, credential_env=config.codex.credential_env)))
    registry.register_student_editor(
        CodexStudentEditor(
            CodexStudentConfig(
                model=config.codex.student.model,
                reasoning_effort=config.codex.student.reasoning_effort,
                retries=config.codex.student.retries,
                timeout_s=config.codex.student.timeout_s,
                max_repair_attempts=int(config.codex.student.max_repair_attempts or 0),
                seed_home=config.state_root,
                credential_env=config.codex.credential_env,
                allowed_patch_roots=config.allowed_patch_roots,
            )
        )
    )
    registry.register_evaluator(
        Contest2026OpenROADEvaluator(
            Contest2026Config(
                design=config.design,
                benchmark_root=config.benchmark_root,
                source_seed=config.source_root or DEFAULT_OPENROAD_SEED,
                build_seed_root=config.build_seed_root,
                allowed_patch_roots=config.allowed_patch_roots,
                require_cpp_patch=config.require_cpp_patch,
                power_reclaim_phase=config.power_reclaim_phase,
                power_reclaim_proportion_percent=config.power_reclaim_proportion_percent,
                power_reclaim_max_moves=config.power_reclaim_max_moves,
                power_stage_tns_ceiling_ns=config.power_stage_tns_ceiling_ns,
                build_jobs=config.build_jobs,
                execution_policy=ExecutionPolicy(config.command_timeout_s, config.command_retries, config.min_free_gb),
            )
        )
    )
    return contract, registry
