from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from goalevolve.core.io import atomic_json, load_json
from goalevolve.p0_campaign import (
    campaign_status,
    create_p0_campaign,
    ensure_p0_campaign_repository_graph,
    extend_campaign_round_limit,
    freeze_measured_baseline,
    p0_template_report,
)


class P0CampaignTests(unittest.TestCase):
    def test_registry_exposes_aes_and_checked_in_p0_assets(self) -> None:
        report = p0_template_report()
        self.assertTrue(report["p0_source_ready"])
        self.assertTrue(report["ast_repository_graph_ready"])
        aes = next(item for item in report["designs"] if item["design"] == "aes_cipher_top")
        self.assertTrue(aes["target_policy_ready"])
        self.assertEqual(aes["template"], "experiments/aes_cipher_top/evolve.json")
        self.assertNotIn("the first 10 rounds enforce", aes["description"])

    def test_aes_p0_init_copies_policy_into_a_private_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            status = create_p0_campaign(
                design="aes_cipher_top",
                output_root=Path(temporary),
                run_id="fresh-p0",
            )
            root = Path(status["campaign_root"])
            profile = load_json(root / "config" / "evolve.json")
            self.assertEqual(status["status"], "initialized_requires_baseline")
            self.assertFalse(profile["campaign_ready"])
            self.assertEqual(profile["planning_mode"], "ast_graph")
            self.assertTrue(profile["repository_graph_enabled"])
            self.assertEqual(profile["power_stage_tns_ceiling_ns"], 60.0)
            self.assertEqual(profile["power_stage_protected_rounds"], 10)
            self.assertTrue(str(profile["source_root"]).endswith("openroad_power/p0/source"))
            self.assertEqual(Path(profile["state_root"]), root / "campaign")
            graph_root = root / "campaign" / "knowledge" / "repository_graph" / "p0"
            self.assertEqual(Path(status["p0"]["repository_graph_root"]), graph_root)
            self.assertTrue((graph_root / "graph.json").is_file())
            self.assertTrue((graph_root / "manifest.json").is_file())
            self.assertEqual(
                status["p0"]["repository_graph_cache"]["copy_mode"],
                "checked_in_p0_graph_copy",
            )

    def test_freeze_baseline_uses_only_current_p0_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = create_p0_campaign(
                design="aes_cipher_top",
                output_root=Path(temporary),
                run_id="freeze-baseline",
            )
            root = Path(initialized["campaign_root"])
            result = {
                "ok": True,
                "metrics": {
                    "tns_abs_ns": 17.0,
                    "dynamic_power_pw": 456.0,
                    "leakage_power_pw": 89.0,
                },
            }
            status = freeze_measured_baseline(root, baseline_result=result)
            profile = load_json(root / "config" / "evolve.json")
            self.assertEqual(status["status"], "baseline_frozen_ready")
            self.assertTrue(profile["campaign_ready"])
            self.assertEqual(profile["baseline_metrics"], result["metrics"])
            self.assertEqual(campaign_status(root)["baseline"]["metrics"], result["metrics"])

    def test_unreviewed_p0_policy_never_becomes_evolution_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = create_p0_campaign(
                design="nvdla_c",
                output_root=Path(temporary),
                run_id="needs-policy",
            )
            root = Path(initialized["campaign_root"])
            status = freeze_measured_baseline(
                root,
                baseline_result={
                    "ok": True,
                    "metrics": {
                        "tns_abs_ns": 81.19,
                        "dynamic_power_pw": 583700000000.0,
                        "leakage_power_pw": 17300000000.0,
                    },
                },
            )
            self.assertEqual(status["status"], "baseline_frozen_target_policy_pending")
            self.assertFalse(load_json(root / "config" / "evolve.json")["campaign_ready"])

    def test_freeze_refuses_to_replace_a_started_campaign_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = create_p0_campaign(
                design="aes_cipher_top",
                output_root=Path(temporary),
                run_id="started",
            )
            root = Path(initialized["campaign_root"])
            (root / "campaign").mkdir(exist_ok=True)
            (root / "campaign" / "parent.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "after evolution has started"):
                freeze_measured_baseline(
                    root,
                    baseline_result={
                        "ok": True,
                        "metrics": {
                            "tns_abs_ns": 1.0,
                            "dynamic_power_pw": 2.0,
                            "leakage_power_pw": 3.0,
                        },
                    },
                )

    def test_thirty_round_horizon_preserves_template_stall_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = create_p0_campaign(
                design="aes_cipher_top",
                output_root=Path(temporary),
                run_id="thirty-rounds",
                campaign_round_limit=30,
            )
            root = Path(initialized["campaign_root"])
            profile = load_json(root / "config" / "evolve.json")
            self.assertEqual(profile["max_campaign_rounds"], 30)
            self.assertEqual(profile["max_consecutive_no_promotion_rounds"], 10)
            self.assertEqual(
                campaign_status(root)["campaign_control"]["max_consecutive_no_promotion_rounds"],
                10,
            )
            extended = extend_campaign_round_limit(root, round_limit=35)
            self.assertEqual(extended["campaign_control"]["max_campaign_rounds"], 35)
            self.assertEqual(
                load_json(root / "config" / "evolve.json")["max_consecutive_no_promotion_rounds"],
                10,
            )

    def test_cli_start_thirty_rounds_freezes_then_runs_the_copied_profile(self) -> None:
        from goalevolve import cli

        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            observed: dict[str, object] = {}

            def fake_baseline(args: object) -> int:
                config = load_json(Path(str(getattr(args, "config"))))
                root = Path(str(config["state_root"]))
                atomic_json(
                    root / "baseline.json",
                    {
                        "ok": True,
                        "metrics": {
                            "tns_abs_ns": 12.5,
                            "dynamic_power_pw": 400.0,
                            "leakage_power_pw": 90.0,
                        },
                    },
                )
                return 0

            def fake_run(args: object) -> int:
                config = load_json(Path(str(getattr(args, "config"))))
                observed["rounds"] = getattr(args, "rounds")
                observed["profile"] = config
                return 0

            args = SimpleNamespace(
                design="aes_cipher_top",
                output_root=str(output_root),
                run_id="cli-start-30",
                planning_mode="ast_graph",
                rounds=30,
            )
            with patch("goalevolve.cli.command_baseline", side_effect=fake_baseline), patch(
                "goalevolve.cli.command_run", side_effect=fake_run
            ):
                self.assertEqual(cli.command_p0_start(args), 0)

            profile = dict(observed["profile"])
            self.assertEqual(observed["rounds"], 30)
            self.assertEqual(profile["max_campaign_rounds"], 30)
            self.assertEqual(profile["max_consecutive_no_promotion_rounds"], 10)
            self.assertTrue(profile["campaign_ready"])
            self.assertEqual(
                profile["baseline_metrics"],
                {
                    "tns_abs_ns": 12.5,
                    "dynamic_power_pw": 400.0,
                    "leakage_power_pw": 90.0,
                },
            )
            self.assertTrue(
                str(profile["source_root"]).endswith("openroad_power/p0/source")
            )

    def test_ast_preflight_rejects_python_310_before_creating_a_campaign(self) -> None:
        from goalevolve.runtime_preflight import require_ast_graph_runtime

        with patch("goalevolve.runtime_preflight.sys.version_info", (3, 10, 20)), patch(
            "goalevolve.runtime_preflight.sys.version", "3.10.20 test"
        ), patch("goalevolve.runtime_preflight.sys.executable", "/env/mlcad-py310/bin/python"):
            with self.assertRaisesRegex(RuntimeError, "Python >= 3.11") as raised:
                require_ast_graph_runtime(operation="unit test")
        self.assertIn("mlcad-py310", str(raised.exception))

    def test_ast_cache_backfill_updates_legacy_campaign_provenance_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            initialized = create_p0_campaign(
                design="aes_cipher_top",
                output_root=Path(temporary),
                run_id="legacy-cache",
            )
            root = Path(initialized["campaign_root"])
            manifest = load_json(root / "p0_campaign.json")
            manifest["p0"]["repository_graph_root"] = "/legacy/project/graph"
            manifest["p0"].pop("repository_graph_cache", None)
            atomic_json(root / "p0_campaign.json", manifest)
            audit = ensure_p0_campaign_repository_graph(root)
            updated = load_json(root / "p0_campaign.json")
            self.assertEqual(updated["p0"]["repository_graph_cache"], audit)
            self.assertEqual(
                Path(updated["p0"]["repository_graph_root"]),
                root / "campaign" / "knowledge" / "repository_graph" / "p0",
            )
            self.assertEqual(updated["repository_graph_cache_history"][-1]["reason"], "campaign_local_p0_cache_verified")
