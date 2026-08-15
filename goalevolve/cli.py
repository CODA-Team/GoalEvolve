from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .config import build_runtime, load_config
from .execution.engine import GoalEvolveEngine
from .core.io import atomic_json, load_json
from .core.models import Parent
from .legacy import LegacyImporter
from .core.provenance import toolchain_fingerprint
from .evaluation.contest2026 import official_four_check
from .evaluation.sfinal import observe_sfinal
from .execution.execution import ExecutionPolicy
from .planning.epd import EvolutionProgramDatabase
from .runtime_preflight import require_ast_graph_runtime
from .evaluation.leaderboard import update_unified_leaderboard
from .dashboard import main as dashboard_main
from .p0_campaign import (
    campaign_paths,
    campaign_status,
    create_p0_campaign,
    ensure_p0_campaign_repository_graph,
    extend_campaign_round_limit,
    freeze_measured_baseline,
    p0_template_report,
)


def _engine(
    config_path: Path,
    *,
    repository_graph_override: str | None = None,
) -> tuple[GoalEvolveEngine, object]:
    config = load_config(config_path)
    planning_mode = (
        ("ast_graph" if repository_graph_override == "on" else "openroad_cards")
        if repository_graph_override is not None
        else config.planning_mode
    )
    contract, registry = build_runtime(config)
    engine = GoalEvolveEngine(
        contract=contract,
        state_root=config.state_root,
        planner=registry.planner(config.planner),
        evaluator=registry.evaluator(config.evaluator),
        workspace_provider=registry.workspace(config.workspace),
        promotion_policy=registry.promotion(config.promotion),
        student_editor=registry.student_editor(config.student_editor),
        teacher=registry.teacher(config.teacher),
        narrator=registry.narrator("codex_narrator"),
        student_ids=config.students,
        max_campaign_rounds=config.max_campaign_rounds,
        max_consecutive_no_promotion_rounds=config.max_consecutive_no_promotion_rounds,
        prefer_execution_champion=config.prefer_execution_champion,
        epd_max_reinforcement_attempts=config.epd_max_reinforcement_attempts,
        historical_seed_cards=config.historical_seed_cards,
        historical_seed_revalidation_schedule=config.historical_seed_revalidation_schedule,
        planning_mode=planning_mode,
        repository_graph_enabled=planning_mode == "ast_graph",
        parent_selection=config.parent_selection,
    )
    return engine, config


def _verify_execution_profile(*, config, evaluator, state_root: Path) -> dict[str, object]:
    """Persist the exact repair-power command profile before state mutation.

    The record is execution provenance only.  It prevents a ready campaign
    from silently evaluating candidates with a smoke-profile command while
    leaving QoR contracts and promotion decisions untouched.
    """
    declared_profile = getattr(config, "declared_power_reclaim_profile", None)
    if declared_profile is None:
        raise RuntimeError("ready campaign lacks declared power-reclaim profile")
    profile_method = getattr(evaluator, "effective_power_reclaim_profile", None)
    if not callable(profile_method):
        raise RuntimeError("evaluator does not expose effective power-reclaim profile")
    declared = dict(declared_profile.to_dict())
    effective = dict(profile_method())
    if effective != declared:
        raise RuntimeError("effective power-reclaim profile differs from declared profile")
    payload: dict[str, object] = {
        "schema_version": "goalevolve.execution-profile.v1",
        "decision_role": "execution_audit_only",
        "declared": declared,
        "effective": effective,
    }
    path = state_root / "execution_profile.json"
    existing = load_json(path)
    if isinstance(existing, dict) and existing and existing != payload:
        raise RuntimeError("execution profile differs from existing campaign")
    atomic_json(path, payload)
    return payload


def _attach_configured_baseline(*, engine: GoalEvolveEngine, config) -> None:
    """Attach the immutable, same-flow p0 artifact selected by configuration.

    A measured campaign must never silently mix a configured pre-route metric
    with a final-flow candidate metric.  The artifact is explicit, validated,
    and becomes the EPD checkpoint source used by Teacher diagnosis.
    """
    root = getattr(config, "baseline_evaluation_root", None)
    if root is None:
        return
    result = load_json(Path(root) / "baseline.json")
    if not isinstance(result, dict) or not bool(result.get("ok")):
        raise RuntimeError(f"configured baseline is incomplete: {Path(root) / 'baseline.json'}")
    metrics = {str(name): float(value) for name, value in dict(result.get("metrics") or {}).items() if isinstance(value, (int, float))}
    required = {spec.name for spec in engine.contract.metrics}
    if not required.issubset(metrics):
        raise RuntimeError(f"configured baseline lacks contract metrics: {','.join(sorted(required - set(metrics)))}")
    for name, expected in engine.contract.baseline_metrics.items():
        actual = metrics.get(name)
        if actual is None or abs(actual - expected) > max(1e-9, abs(expected) * 1e-9):
            raise RuntimeError(f"configured baseline metric differs from frozen contract: {name}={actual}, expected={expected}")
    parent = engine._load_parent()
    distance, _, _ = engine.contract.evaluate(metrics)
    engine._epd().attach_baseline_evaluation(
        parent=parent,
        metrics=metrics,
        goal_distance=distance,
        artifacts={str(name): str(value) for name, value in dict(result.get("artifacts") or {}).items()},
        passed=True,
    )


def _attach_initial_parent(*, engine: GoalEvolveEngine, config) -> None:
    """Start a new frozen contract from a previously validated source parent.

    This is intentionally explicit because changing the metric set creates a
    new campaign contract.  The old campaign's source is copied as a parent,
    while its QoR metrics are supplied in the new units/contract and remain
    subject to every later candidate's full-flow integrity checks.
    """
    source = config.initial_parent_source_root
    metrics = config.initial_parent_metrics
    if source is None or metrics is None or not config.initial_parent_id:
        return
    marker = engine.state_root / "initial_parent.json"
    if marker.is_file():
        return
    required = {spec.name for spec in engine.contract.metrics}
    if not required.issubset(metrics):
        raise RuntimeError(f"initial parent lacks contract metrics: {','.join(sorted(required - set(metrics)))}")
    if not source.is_dir():
        raise RuntimeError(f"initial parent source is missing: {source}")
    distance, _, _ = engine.contract.evaluate(metrics)
    parent = Parent(
        config.initial_parent_id,
        dict(metrics),
        config.initial_parent_source_commit or "imported_parent",
        config.initial_parent_source_hash or "imported_parent",
        distance,
        config.initial_parent_evaluation_mode,
    )
    promote = getattr(engine.workspace_provider, "promote_candidate", None)
    if not callable(promote):
        raise RuntimeError("workspace provider cannot materialize initial parent")
    current = engine._load_parent()
    initial_artifacts = dict(config.initial_parent_artifacts or {})
    promote(
        state_root=engine.state_root,
        parent=current,
        candidate=parent,
        candidate_source=source,
        candidate_artifacts=initial_artifacts,
    )
    engine._refresh_promoted_parent_repository_graph(parent=parent)
    atomic_json(engine.state_root / "parent.json", parent.to_dict())
    atomic_json(marker, {
        "parent": parent.to_dict(),
        "source": str(source),
        "artifacts": initial_artifacts,
        "decision_role": "contract_migration_explicit",
    })


def command_run(args: argparse.Namespace) -> int:
    engine, config = _engine(
        Path(args.config).resolve(),
        repository_graph_override=getattr(args, "repository_graph", None),
    )
    if engine.effective_planning_mode == "ast_graph":
        require_ast_graph_runtime(operation=f"AST-planned run for {config.design!r}")
    if config.evaluator == "contest_openroad" and config.campaign_ready is not True:
        raise RuntimeError(
            f"profile for {config.design!r} is not ready for evolution: measure the baseline, "
            "set absolute target_metrics, then set campaign_ready=true"
        )
    if (
        config.evaluator == "contest_openroad"
        and bool(getattr(config, "enforce_declared_power_reclaim_profile", False))
    ):
        _verify_execution_profile(
            config=config,
            evaluator=engine.evaluator,
            state_root=engine.state_root,
        )
    # initialize is idempotent and rejects a resume with a different frozen contract.
    engine.initialize(baseline_metrics=config.baseline_metrics)
    _attach_configured_baseline(engine=engine, config=config)
    _attach_initial_parent(engine=engine, config=config)
    parent = engine.run(rounds=args.rounds)
    atomic_json(engine.state_root / "toolchain.json", toolchain_fingerprint(source_root=config.source_root))
    print(json.dumps({
        "state_root": str(engine.state_root),
        "planning_mode": engine.effective_planning_mode,
        "repository_graph_enabled": engine.repository_graph_enabled,
        "final_parent": parent.to_dict(),
    }, ensure_ascii=False, indent=2))
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    root = Path(args.state_root).resolve()
    if root.exists() and args.clean:
        shutil.rmtree(root)
    config = {
        "design": "smoke_design",
        "state_root": str(root),
        "baseline_metrics": {"tns_abs_ns": 100.0, "leakage_power_pw": 200.0},
        "target_metrics": {"tns_abs_ns": 20.0, "leakage_power_pw": 160.0},
        "metric_weights": {"tns_abs_ns": 1.0, "leakage_power_pw": 0.5},
        "planner": "diverse_planner",
        "evaluator": "mock",
        "workspace": "isolated_workspace",
        "promotion": "strict_evidence",
        "students": ["student_1", "student_2", "student_3", "student_4"],
    }
    config_path = root.parent / "smoke_config.json"
    atomic_json(config_path, config)
    args.config = str(config_path)
    return command_run(args)


def command_import_legacy(args: argparse.Namespace) -> int:
    destination = Path(args.state_root).resolve() / "legacy" / "import.json"
    records = LegacyImporter().import_manifest(manifest_path=Path(args.manifest).resolve(), destination=destination)
    print(json.dumps({"imported": len(records), "destination": str(destination)}, ensure_ascii=False))
    return 0


def command_official_check(args: argparse.Namespace) -> int:
    benchmark_root = Path(args.benchmark_root).resolve()
    pre_opt = benchmark_root / args.design
    post_opt = Path(args.post_opt).resolve()
    output_log = Path(args.output_log).resolve() if args.output_log else post_opt / "official_4of4.log"
    passed, detail = official_four_check(
        pre_opt=pre_opt,
        post_opt=post_opt,
        output_log=output_log,
        policy=ExecutionPolicy(timeout_s=args.timeout_s, retries=0, min_free_gb=0.0),
    )
    print(json.dumps({"passed": passed, "detail": detail, "log": str(output_log)}, ensure_ascii=False))
    return 0 if passed else 1


def command_baseline(args: argparse.Namespace) -> int:
    engine, config = _engine(Path(args.config).resolve())
    if (
        config.evaluator == "contest_openroad"
        and config.campaign_ready is True
        and bool(getattr(config, "enforce_declared_power_reclaim_profile", False))
    ):
        _verify_execution_profile(
            config=config,
            evaluator=engine.evaluator,
            state_root=engine.state_root,
        )
    # A baseline command is allowed to initialize state, but never to create a
    # candidate or alter the source seed. It attaches real p0 evidence to EPD.
    parent = engine.initialize(baseline_metrics=config.baseline_metrics)
    contract = engine.contract
    evaluator = engine.evaluator
    measure = getattr(evaluator, "evaluate_baseline", None)
    if not callable(measure):
        raise RuntimeError(f"evaluator {config.evaluator} does not implement baseline measurement")
    # Baseline profiles reserve state_root for the measurement itself, so the
    # persisted record is always <state_root>/baseline.json by default.
    output = Path(args.output).resolve() if args.output else config.state_root
    result = measure(contract=contract, output=output)
    atomic_json(output / "baseline.json", result)
    measured_metrics = {str(name): float(value) for name, value in dict(result.get("metrics") or {}).items() if isinstance(value, (int, float))}
    distance, _, _ = contract.evaluate(measured_metrics)
    engine._epd().attach_baseline_evaluation(
        parent=parent,
        metrics=measured_metrics,
        goal_distance=distance,
        artifacts={str(name): str(value) for name, value in dict(result.get("artifacts") or {}).items()},
        passed=bool(result.get("ok")),
    )
    print(json.dumps({"output": str(output), **result}, ensure_ascii=False, indent=2))
    return 0 if bool(result.get("ok")) else 1


def command_sfinal_observe(args: argparse.Namespace) -> int:
    report = observe_sfinal(
        design=args.design,
        benchmark_dir=Path(args.benchmark_root).resolve() / args.design,
        candidate_dir=Path(args.post_opt).resolve(),
        output=Path(args.output).resolve() if args.output else None,
    )
    score = dict(report["score"])
    print(f"[GoalEvolve][observer][Sfinal] design={args.design} Sfinal={float(score['Sfinal']):.12g}", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_leaderboard(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root).resolve()
    report = update_unified_leaderboard(
        leaderboard_root=output_root,
        state_roots=(Path(path).resolve() for path in args.state_root),
        top_k=args.top_k,
    )
    print(json.dumps({
        "output_root": str(output_root),
        "campaign_count": len(report["campaigns"]),
        "row_count": len(report["rows"]),
        "ranking": report["ranking_scope"],
    }, ensure_ascii=False, indent=2))
    return 0


def command_dashboard(args: argparse.Namespace) -> int:
    dashboard_args = ["--state-root", args.state_root, "--host", args.host, "--port", str(args.port)]
    if args.once:
        dashboard_args.append("--once")
    return dashboard_main(dashboard_args)


def command_p0_list(_args: argparse.Namespace) -> int:
    """List the reviewed P0 policies available to local campaign users."""
    print(json.dumps(p0_template_report(), ensure_ascii=False, indent=2))
    return 0


def command_p0_init(args: argparse.Namespace) -> int:
    """Materialise a design-selected P0 policy in a fresh run directory."""
    report = create_p0_campaign(
        design=args.design,
        output_root=Path(args.output_root),
        run_id=args.run_id,
        planning_mode=args.planning_mode,
        campaign_round_limit=args.max_rounds,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_p0_baseline(args: argparse.Namespace) -> int:
    """Measure P0 then freeze precisely those metrics into its copied contract."""
    campaign_root = Path(args.campaign)
    paths = campaign_paths(campaign_root)
    measured = load_json(paths["baseline_root"] / "baseline.json")
    if not isinstance(measured, dict) or not bool(measured.get("ok")):
        # A failed attempt is not a frozen baseline and may be rerun.  A
        # successful file is instead consumed below, which makes the
        # measurement-to-freeze transition restart-safe.
        result = command_baseline(
            argparse.Namespace(config=str(paths["baseline_config"]), output=None)
        )
        if result != 0:
            return result
        measured = load_json(paths["baseline_root"] / "baseline.json")
    if not isinstance(measured, dict):
        raise RuntimeError("successful P0 baseline did not create baseline.json")
    report = freeze_measured_baseline(campaign_root, baseline_result=measured)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_p0_run(args: argparse.Namespace) -> int:
    """Run or resume only a P0 campaign whose measured contract was frozen."""
    if args.rounds <= 0:
        raise ValueError("P0 run rounds must be a positive integer")
    status = campaign_status(Path(args.campaign))
    if status.get("status") != "baseline_frozen_ready":
        raise RuntimeError(
            "P0 campaign is not evolution-ready; run `p0 baseline --campaign ...` first "
            "(or review its target-policy status)"
        )
    paths = campaign_paths(Path(args.campaign))
    ensure_p0_campaign_repository_graph(Path(args.campaign))
    return command_run(
        argparse.Namespace(
            config=str(paths["evolve_config"]),
            rounds=args.rounds,
            repository_graph=None,
        )
    )


def command_p0_start(args: argparse.Namespace) -> int:
    """Create, measure, freeze and start a brand-new selected P0 campaign."""
    created = create_p0_campaign(
        design=args.design,
        output_root=Path(args.output_root),
        run_id=args.run_id,
        planning_mode=args.planning_mode,
        campaign_round_limit=args.rounds,
    )
    campaign_root = str(created["campaign_root"])
    baseline_status = command_p0_baseline(argparse.Namespace(campaign=campaign_root))
    if baseline_status != 0:
        return baseline_status
    return command_p0_run(
        argparse.Namespace(campaign=campaign_root, rounds=args.rounds)
    )


def command_p0_status(args: argparse.Namespace) -> int:
    """Show the copied policy, P0 provenance and lifecycle state."""
    print(json.dumps(campaign_status(Path(args.campaign)), ensure_ascii=False, indent=2))
    return 0


def command_p0_extend(args: argparse.Namespace) -> int:
    """Increase only the copied campaign's local execution horizon."""
    report = extend_campaign_round_limit(
        Path(args.campaign), round_limit=args.max_rounds
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="goalevolve-v2")
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="run a configurable multi-round campaign")
    run.add_argument("--config", required=True)
    run.add_argument("--rounds", type=int, default=1)
    run.add_argument(
        "--repository-graph",
        choices=("on", "off"),
        default=None,
        help="override the profile's P0-rooted AST repository-graph setting for an ablation",
    )
    run.set_defaults(func=command_run)
    smoke = subs.add_parser("smoke", help="run a four-student multi-round mock campaign")
    smoke.add_argument("--state-root", default="state/smoke")
    smoke.add_argument("--rounds", type=int, default=3)
    smoke.add_argument("--clean", action="store_true")
    smoke.set_defaults(func=command_smoke)
    legacy = subs.add_parser("import-legacy", help="import an explicit metadata-only legacy manifest")
    legacy.add_argument("--manifest", required=True)
    legacy.add_argument("--state-root", required=True)
    legacy.set_defaults(func=command_import_legacy)
    check = subs.add_parser("official-check", help="run the vendored reference official 4/4 checker")
    check.add_argument("--design", required=True)
    check.add_argument("--benchmark-root", required=True)
    check.add_argument("--post-opt", required=True)
    check.add_argument("--output-log")
    check.add_argument("--timeout-s", type=int, default=900)
    check.set_defaults(func=command_official_check)
    baseline = subs.add_parser("baseline", help="measure the immutable seed with the contest flow and official 4/4 gate")
    baseline.add_argument("--config", required=True)
    baseline.add_argument("--output")
    baseline.set_defaults(func=command_baseline)
    sfinal = subs.add_parser("sfinal-observe", help="compute and record official Sfinal for comparison only; it never affects evolution decisions")
    sfinal.add_argument("--design", required=True)
    sfinal.add_argument("--benchmark-root", required=True)
    sfinal.add_argument("--post-opt", required=True)
    sfinal.add_argument("--output")
    sfinal.set_defaults(func=command_sfinal_observe)
    leaderboard = subs.add_parser("leaderboard", help="update the verified cross-design Top-K QoR leaderboard")
    leaderboard.add_argument("--state-root", action="append", default=[], help="campaign state root to register; repeat for multiple campaigns")
    leaderboard.add_argument("--output-root", default="runtime/leaderboard")
    leaderboard.add_argument("--top-k", type=int, default=5)
    leaderboard.set_defaults(func=command_leaderboard)
    dashboard = subs.add_parser("dashboard", help="serve a read-only local AE-3 campaign dashboard")
    dashboard.add_argument("--state-root", required=True, help="AE-3 campaign state root")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8080)
    dashboard.add_argument("--once", action="store_true", help="print one dashboard snapshot and exit")
    dashboard.set_defaults(func=command_dashboard)
    p0 = subs.add_parser(
        "p0",
        help="create isolated, design-selected P0 campaigns from reviewed templates",
    )
    p0_subs = p0.add_subparsers(dest="p0_command", required=True)
    p0_list = p0_subs.add_parser("list", help="list P0 templates and readiness")
    p0_list.set_defaults(func=command_p0_list)
    p0_init = p0_subs.add_parser(
        "init",
        help="copy one selected P0 template into a fresh, isolated campaign directory",
    )
    p0_init.add_argument("--design", required=True, help="design shown by `p0 list`")
    p0_init.add_argument("--run-id", required=True, help="unique local run identifier")
    p0_init.add_argument("--output-root", default="outputs/p0_campaigns")
    p0_init.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="local maximum round horizon; preserves the template's stall-stop threshold",
    )
    p0_init.add_argument(
        "--planning-mode",
        choices=("ast_graph", "openroad_cards"),
        default="ast_graph",
        help="AST graph is the P0 default; card-only is an explicit ablation",
    )
    p0_init.set_defaults(func=command_p0_init)
    p0_baseline = p0_subs.add_parser(
        "baseline",
        help="measure and freeze the selected P0 campaign's official baseline",
    )
    p0_baseline.add_argument("--campaign", required=True, help="directory printed by `p0 init`")
    p0_baseline.set_defaults(func=command_p0_baseline)
    p0_run = p0_subs.add_parser("run", help="run or resume a frozen P0 campaign")
    p0_run.add_argument("--campaign", required=True, help="directory printed by `p0 init`")
    p0_run.add_argument("--rounds", type=int, default=1)
    p0_run.set_defaults(func=command_p0_run)
    p0_extend = p0_subs.add_parser(
        "extend",
        help="increase a copied P0 campaign's maximum round horizon only",
    )
    p0_extend.add_argument("--campaign", required=True)
    p0_extend.add_argument("--max-rounds", required=True, type=int)
    p0_extend.set_defaults(func=command_p0_extend)
    p0_status = p0_subs.add_parser("status", help="print P0 campaign provenance and state")
    p0_status.add_argument("--campaign", required=True)
    p0_status.set_defaults(func=command_p0_status)
    p0_start = p0_subs.add_parser(
        "start",
        help="create, baseline, freeze, and run a new selected P0 campaign",
    )
    p0_start.add_argument("--design", required=True, help="design shown by `p0 list`")
    p0_start.add_argument("--run-id", required=True, help="unique local run identifier")
    p0_start.add_argument("--output-root", default="outputs/p0_campaigns")
    p0_start.add_argument(
        "--planning-mode",
        choices=("ast_graph", "openroad_cards"),
        default="ast_graph",
    )
    p0_start.add_argument("--rounds", type=int, default=1)
    p0_start.set_defaults(func=command_p0_start)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
