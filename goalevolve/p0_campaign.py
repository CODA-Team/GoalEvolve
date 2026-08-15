"""Create and operate isolated, P0-rooted evolution campaigns.

The public experiment profiles describe policies (targets, allowed source
roots, and optimisation settings).  They must not be used as mutable runtime
state.  This module materialises a selected policy into a self-contained
campaign directory so that a local run can be resumed or later packaged for
artifact evaluation without changing a reviewed template.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Mapping

from .config import PLANNING_MODES, PROJECT_ROOT, _load_raw_config
from .core.io import atomic_json, load_json, sha256_file
from .planning.repository_graph import RepositoryGraphIndex
from .runtime_preflight import require_ast_graph_runtime


P0_SOURCE_ROOT = PROJECT_ROOT / "artifact_evaluation" / "lineage" / "openroad_power" / "p0" / "source"
P0_REPOSITORY_GRAPH_ROOT = PROJECT_ROOT / "artifact_evaluation" / "lineage" / "openroad_power" / "p0" / "repository_graph"
P0_TEMPLATE_REGISTRY = PROJECT_ROOT / "experiments" / "p0_templates.json"
CAMPAIGN_MANIFEST = "p0_campaign.json"
BASELINE_CONFIG = "config/baseline.json"
EVOLVE_CONFIG = "config/evolve.json"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_PROFILE_PATH_FIELDS = frozenset(
    {
        "build_seed_root",
        "benchmark_root",
        "historical_seed_cards",
        "initial_parent_source_root",
    }
)
_INITIAL_PARENT_FIELDS = frozenset(
    {
        "initial_parent_id",
        "initial_parent_source_root",
        "initial_parent_metrics",
        "initial_parent_source_commit",
        "initial_parent_source_hash",
        "initial_parent_evaluation_mode",
        "initial_parent_artifacts",
    }
)


def _utc_now() -> str:
    """Return a UTC timestamp on every supported Python version.

    ``datetime.UTC`` was added in Python 3.11.  GoalEvolve's documented
    runtime also supports the project's Python 3.10 environment, where the
    equivalent portable spelling is ``timezone.utc``.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _registry_payload() -> dict[str, Any]:
    payload = load_json(P0_TEMPLATE_REGISTRY)
    if not isinstance(payload, dict) or not isinstance(payload.get("designs"), dict):
        raise RuntimeError(f"invalid P0 template registry: {P0_TEMPLATE_REGISTRY}")
    return payload


def p0_templates() -> dict[str, dict[str, Any]]:
    """Return checked-in P0 campaign template entries keyed by benchmark."""
    designs = dict(_registry_payload()["designs"])
    resolved: dict[str, dict[str, Any]] = {}
    for design, entry in designs.items():
        if not isinstance(entry, Mapping):
            raise RuntimeError(f"invalid P0 template entry for {design!r}")
        template = Path(str(entry.get("template") or ""))
        if not template or template.is_absolute() or ".." in template.parts:
            raise RuntimeError(f"invalid relative template for {design!r}")
        template_path = (PROJECT_ROOT / "experiments" / template).resolve()
        if not template_path.is_file() or PROJECT_ROOT not in template_path.parents:
            raise RuntimeError(f"P0 template is missing for {design!r}: {template_path}")
        resolved[str(design)] = {
            "design": str(design),
            "template_path": template_path,
            "description": str(entry.get("description") or ""),
            "target_policy_ready": bool(entry.get("target_policy_ready", False)),
        }
    return resolved


def p0_template_report() -> dict[str, object]:
    """Return a safe, human-readable report used by ``p0 list``."""
    templates = p0_templates()
    return {
        "p0_source_root": str(P0_SOURCE_ROOT),
        "p0_source_ready": (P0_SOURCE_ROOT / "CMakeLists.txt").is_file(),
        "ast_repository_graph_root": str(P0_REPOSITORY_GRAPH_ROOT),
        "ast_repository_graph_ready": P0_REPOSITORY_GRAPH_ROOT.is_dir(),
        "designs": [
            {
                "design": entry["design"],
                "template": str(entry["template_path"].relative_to(PROJECT_ROOT)),
                "target_policy_ready": entry["target_policy_ready"],
                "description": entry["description"],
            }
            for _, entry in sorted(templates.items())
        ],
    }


def _absolute_profile_paths(raw: Mapping[str, Any], *, template_root: Path) -> dict[str, Any]:
    """Flatten template-relative paths before copying a profile to runtime."""
    profile = dict(raw)
    for key in _PROFILE_PATH_FIELDS:
        value = profile.get(key)
        if not value:
            continue
        path = Path(str(value)).expanduser()
        profile[key] = str(path if path.is_absolute() else (template_root / path).resolve())
    return profile


def _base_profile(*, template_path: Path, planning_mode: str) -> dict[str, Any]:
    if planning_mode not in PLANNING_MODES:
        raise ValueError("planning_mode must be one of: " + ", ".join(sorted(PLANNING_MODES)))
    raw = _load_raw_config(template_path)
    profile = _absolute_profile_paths(raw, template_root=template_path.parent)
    for key in _INITIAL_PARENT_FIELDS:
        profile.pop(key, None)
    profile.pop("state_root", None)
    profile.pop("baseline_evaluation_root", None)
    profile["source_root"] = str(P0_SOURCE_ROOT)
    profile["planning_mode"] = planning_mode
    profile["repository_graph_enabled"] = planning_mode == "ast_graph"
    return profile


def _require_p0_assets(*, planning_mode: str) -> None:
    if not (P0_SOURCE_ROOT / "CMakeLists.txt").is_file():
        raise RuntimeError(f"P0 OpenROAD source is missing: {P0_SOURCE_ROOT}")
    if planning_mode == "ast_graph":
        require_ast_graph_runtime(operation="P0 AST campaign creation")
        if not P0_REPOSITORY_GRAPH_ROOT.is_dir():
            raise RuntimeError(f"P0 AST repository graph is missing: {P0_REPOSITORY_GRAPH_ROOT}")


def _campaign_paths(campaign_root: Path) -> dict[str, Path]:
    root = campaign_root.resolve()
    return {
        "root": root,
        "manifest": root / CAMPAIGN_MANIFEST,
        "baseline_config": root / BASELINE_CONFIG,
        "evolve_config": root / EVOLVE_CONFIG,
        "baseline_root": root / "baseline",
        "campaign_root": root / "campaign",
    }


def _apply_campaign_round_limit(
    profile: Mapping[str, Any], *, round_limit: int | None
) -> tuple[dict[str, Any], dict[str, int]]:
    """Set a campaign-local horizon without changing its QoR policy.

    ``max_campaign_rounds`` is an execution scheduling control, not a
    goal-contract or promotion-policy change.  A local P0 user who asks for
    thirty rounds therefore receives a copied profile that can reach round
    thirty, while the reviewed template remains untouched.  A solved QoR
    contract and the template's no-promotion watchdog both still end normally
    before the requested horizon.
    """
    result = dict(profile)
    inherited_limit = int(result.get("max_campaign_rounds") or 0)
    inherited_stall_limit = int(result.get("max_consecutive_no_promotion_rounds") or 0)
    if round_limit is not None and round_limit <= 0:
        raise ValueError("campaign round limit must be a positive integer")
    effective_limit = max(inherited_limit, int(round_limit or 0))
    if effective_limit <= 0:
        # An unbounded reviewed template remains unbounded unless the user
        # explicitly chooses a finite local horizon.
        return result, {
            "max_campaign_rounds": 0,
            "max_consecutive_no_promotion_rounds": inherited_stall_limit,
        }
    result["max_campaign_rounds"] = effective_limit
    return result, {
        "max_campaign_rounds": effective_limit,
        "max_consecutive_no_promotion_rounds": inherited_stall_limit,
    }


def create_p0_campaign(
    *,
    design: str,
    output_root: Path,
    run_id: str,
    planning_mode: str = "ast_graph",
    campaign_round_limit: int | None = None,
) -> dict[str, object]:
    """Copy one reviewed P0 policy into a new, isolated runtime directory."""
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id may contain only letters, digits, '.', '_' and '-'")
    templates = p0_templates()
    if design not in templates:
        raise ValueError("unknown P0 design: " + design + "; use `p0 list`")
    _require_p0_assets(planning_mode=planning_mode)
    entry = templates[design]
    paths = _campaign_paths(Path(output_root).resolve() / design / run_id)
    if paths["root"].exists():
        raise RuntimeError(f"P0 campaign directory already exists: {paths['root']}")

    template_path = Path(entry["template_path"])
    profile = _base_profile(template_path=template_path, planning_mode=planning_mode)
    if str(profile.get("design") or "") != design:
        raise RuntimeError(f"P0 template design mismatch: expected {design}, got {profile.get('design')}")
    profile["campaign_ready"] = False
    profile, round_controls = _apply_campaign_round_limit(
        profile, round_limit=campaign_round_limit
    )

    local_graph_root: Path | None = None
    local_graph_audit: dict[str, object] | None = None
    if planning_mode == "ast_graph":
        # Materialize before baseline measurement or any mutable campaign
        # state.  This is also the compatibility validation for the cached P0
        # graph that all later incremental parent graphs will reuse.
        p0_graph = RepositoryGraphIndex(state_root=paths["campaign_root"])
        copied_graph = p0_graph.ensure_campaign_p0_cache()
        local_graph_root = copied_graph.artifact_root
        local_graph_audit = {
            "artifact_root": str(copied_graph.artifact_root),
            "source_hash": copied_graph.source_hash,
            "base_source_hash": copied_graph.base_source_hash,
            "allowed_patch_roots": list(copied_graph.allowed_patch_roots),
            "copy_mode": "checked_in_p0_graph_copy",
        }

    baseline_profile = dict(profile)
    baseline_profile["state_root"] = str(paths["baseline_root"])
    evolve_profile = dict(profile)
    evolve_profile["state_root"] = str(paths["campaign_root"])
    evolve_profile["baseline_evaluation_root"] = str(paths["baseline_root"])

    template_digest = sha256_file(template_path)
    manifest: dict[str, object] = {
        "schema_version": "goalevolve.p0-campaign.v1",
        "created_at": _utc_now(),
        "design": design,
        "run_id": run_id,
        "planning_mode": planning_mode,
        "repository_graph_enabled": planning_mode == "ast_graph",
        "target_policy_ready": bool(entry["target_policy_ready"]),
        "status": "initialized_requires_baseline",
        "campaign_control": {
            **round_controls,
            "completion_exception": "goal_contract_met",
            "decision_role": "local_execution_horizon_only",
        },
        "template": {
            "path": str(template_path.relative_to(PROJECT_ROOT)),
            "sha256": template_digest,
        },
        "p0": {
            "source_root": str(P0_SOURCE_ROOT),
            "source_manifest": str(P0_SOURCE_ROOT.parent / "source_manifest.json"),
            "repository_graph_root": str(local_graph_root) if local_graph_root else None,
            "repository_graph_source_root": str(P0_REPOSITORY_GRAPH_ROOT) if planning_mode == "ast_graph" else None,
            "repository_graph_cache": local_graph_audit,
        },
        "paths": {
            "baseline_config": str(paths["baseline_config"]),
            "evolve_config": str(paths["evolve_config"]),
            "baseline_root": str(paths["baseline_root"]),
            "campaign_root": str(paths["campaign_root"]),
        },
    }
    atomic_json(paths["baseline_config"], baseline_profile)
    atomic_json(paths["evolve_config"], evolve_profile)
    atomic_json(paths["manifest"], manifest)
    return campaign_status(paths["root"])


def _load_manifest(campaign_root: Path) -> tuple[dict[str, object], dict[str, Path]]:
    paths = _campaign_paths(campaign_root)
    manifest = load_json(paths["manifest"])
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "goalevolve.p0-campaign.v1":
        raise RuntimeError(f"not a GoalEvolve P0 campaign: {paths['root']}")
    if not paths["baseline_config"].is_file() or not paths["evolve_config"].is_file():
        raise RuntimeError(f"P0 campaign profiles are incomplete: {paths['root']}")
    return manifest, paths


def freeze_measured_baseline(campaign_root: Path, *, baseline_result: Mapping[str, Any]) -> dict[str, object]:
    """Freeze a measured P0 result into the copied evolve profile.

    The caller has already run the official baseline flow.  This operation is
    intentionally refused after evolution state exists: a campaign contract
    never changes beneath completed rounds.
    """
    manifest, paths = _load_manifest(campaign_root)
    if (paths["campaign_root"] / "contract.json").exists() or (paths["campaign_root"] / "parent.json").exists():
        raise RuntimeError("cannot replace a P0 baseline after evolution has started; create a new P0 campaign")
    if not bool(baseline_result.get("ok")):
        raise RuntimeError("cannot freeze an unsuccessful P0 baseline")
    evolve = load_json(paths["evolve_config"])
    if not isinstance(evolve, dict):
        raise RuntimeError(f"invalid P0 evolve profile: {paths['evolve_config']}")
    metric_names = set(dict(evolve.get("target_metrics") or {}))
    observed = dict(baseline_result.get("metrics") or {})
    missing = sorted(name for name in metric_names if not isinstance(observed.get(name), (int, float)))
    if missing:
        raise RuntimeError("measured P0 baseline lacks target metrics: " + ", ".join(missing))
    frozen = {name: float(observed[name]) for name in sorted(metric_names)}
    evolve["baseline_metrics"] = frozen
    evolve["campaign_ready"] = bool(manifest.get("target_policy_ready"))
    evolve["baseline_evaluation_root"] = str(paths["baseline_root"])
    atomic_json(paths["evolve_config"], evolve)

    updated = dict(manifest)
    updated["status"] = "baseline_frozen_ready" if evolve["campaign_ready"] else "baseline_frozen_target_policy_pending"
    updated["baseline"] = {
        "measured_at": _utc_now(),
        "metrics": frozen,
        "result": str(paths["baseline_root"] / "baseline.json"),
    }
    atomic_json(paths["manifest"], updated)
    return campaign_status(paths["root"])


def extend_campaign_round_limit(
    campaign_root: Path, *, round_limit: int
) -> dict[str, object]:
    """Explicitly extend a copied P0 campaign's local execution horizon.

    This may only increase a limit.  Its frozen measured baseline, targets,
    source root, planning mode and promotion policy remain byte-for-byte
    untouched.
    """
    if round_limit <= 0:
        raise ValueError("campaign round limit must be a positive integer")
    manifest, paths = _load_manifest(campaign_root)
    evolve = load_json(paths["evolve_config"])
    if not isinstance(evolve, dict):
        raise RuntimeError(f"invalid P0 evolve profile: {paths['evolve_config']}")
    current = int(evolve.get("max_campaign_rounds") or 0)
    if current <= 0:
        raise RuntimeError("cannot extend an unbounded P0 campaign")
    if round_limit < current:
        raise ValueError(
            f"campaign round limit cannot decrease: current={current}, requested={round_limit}"
        )
    if round_limit == current:
        return campaign_status(paths["root"])
    evolve["max_campaign_rounds"] = round_limit
    atomic_json(paths["evolve_config"], evolve)
    updated = dict(manifest)
    updated["campaign_control"] = {
        "max_campaign_rounds": round_limit,
        "max_consecutive_no_promotion_rounds": int(
            evolve.get("max_consecutive_no_promotion_rounds") or 0
        ),
        "completion_exception": "goal_contract_met",
        "decision_role": "local_execution_horizon_only",
    }
    history = list(updated.get("campaign_control_history") or [])
    history.append({"updated_at": _utc_now(), "extended_to_round": round_limit})
    updated["campaign_control_history"] = history
    atomic_json(paths["manifest"], updated)
    return campaign_status(paths["root"])


def campaign_paths(campaign_root: Path) -> dict[str, Path]:
    """Validate and return immutable locations for a P0 campaign."""
    _, paths = _load_manifest(campaign_root)
    return paths


def ensure_p0_campaign_repository_graph(campaign_root: Path) -> dict[str, object] | None:
    """Backfill and record the local AST cache for a P0 campaign resume.

    Campaigns made before local graph ownership was introduced retain their
    original frozen QoR contract.  Only cache provenance is added here.
    """

    manifest, paths = _load_manifest(campaign_root)
    evolve = load_json(paths["evolve_config"])
    planning_mode = str(
        (evolve or {}).get("planning_mode")
        if isinstance(evolve, Mapping)
        else manifest.get("planning_mode") or ""
    )
    if planning_mode != "ast_graph":
        return None
    require_ast_graph_runtime(operation="P0 AST campaign resume")
    graph = RepositoryGraphIndex(state_root=paths["campaign_root"]).ensure_campaign_p0_cache()
    audit: dict[str, object] = {
        "artifact_root": str(graph.artifact_root),
        "source_hash": graph.source_hash,
        "base_source_hash": graph.base_source_hash,
        "allowed_patch_roots": list(graph.allowed_patch_roots),
        "copy_mode": "checked_in_p0_graph_copy",
    }
    updated = dict(manifest)
    p0 = dict(updated.get("p0") or {})
    if (
        p0.get("repository_graph_root") != audit["artifact_root"]
        or p0.get("repository_graph_cache") != audit
    ):
        p0["repository_graph_root"] = audit["artifact_root"]
        p0["repository_graph_source_root"] = str(P0_REPOSITORY_GRAPH_ROOT)
        p0["repository_graph_cache"] = audit
        updated["p0"] = p0
        history = list(updated.get("repository_graph_cache_history") or [])
        history.append(
            {
                "updated_at": _utc_now(),
                "artifact_root": audit["artifact_root"],
                "source_hash": audit["source_hash"],
                "reason": "campaign_local_p0_cache_verified",
            }
        )
        updated["repository_graph_cache_history"] = history[-20:]
        atomic_json(paths["manifest"], updated)
    return audit


def campaign_status(campaign_root: Path) -> dict[str, object]:
    manifest, paths = _load_manifest(campaign_root)
    result = dict(manifest)
    result["campaign_root"] = str(paths["root"])
    result["baseline_result_present"] = (paths["baseline_root"] / "baseline.json").is_file()
    result["evolution_started"] = (paths["campaign_root"] / "parent.json").is_file()
    return result
