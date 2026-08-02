from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from goalevolve.core.io import atomic_json
from goalevolve.core.contracts import build_contract


def _write_campaign(root: Path) -> None:
    contract = build_contract(
        design="demo_design",
        baseline_metrics={
            "tns_abs_ns": 100.0,
            "dynamic_power_pw": 300.0,
            "leakage_power_pw": 90.0,
        },
        target_metrics={
            "tns_abs_ns": 50.0,
            "dynamic_power_pw": 180.0,
            "leakage_power_pw": 60.0,
        },
    )
    atomic_json(root / "contract.json", contract.to_dict())
    atomic_json(
        root / "parent.json",
        {
            "parent_id": "round_001:student_1",
            "metrics": {
                "tns_abs_ns": 70.0,
                "dynamic_power_pw": 200.0,
                "leakage_power_pw": 70.0,
            },
        },
    )

    first = root / "rounds" / "round_001"
    atomic_json(
        first / "teacher_plan.json",
        {
            "teacher_ok": True,
            "diagnosis": {"dominant_bottleneck": "tns_abs_ns"},
            "hypotheses": [
                {
                    "student_id": "student_1",
                    "hypothesis_id": "timing_hypothesis",
                    "claim": "Prioritize the critical timing path.",
                    "source_hooks": ["src/rsz/RecoverTiming.cc"],
                    "timing_recipe_id": "tns_global",
                    "student_role": "explorer",
                    "role_mode": "fresh_exploration",
                    "epd_record_ids": [],
                },
                {
                    "student_id": "student_2",
                    "hypothesis_id": "power_hypothesis",
                    "claim": "Preserve the power recovery frontier.",
                    "source_hooks": ["src/rsz/RecoverPower.cc"],
                    "timing_recipe_id": "power_only",
                },
            ],
        },
    )
    atomic_json(
        first / "students" / "student_1" / "artifacts" / "candidate.json",
        {
            "student_id": "student_1",
            "metrics": {
                "tns_abs_ns": 70.0,
                "dynamic_power_pw": 200.0,
                "leakage_power_pw": 70.0,
                "drv_count": 0.0,
            },
            "checks": [
                {"name": name, "passed": True}
                for name in ("build", "flow", "metrics", "lec")
            ],
            "hypothesis": {
                "hypothesis_id": "timing_hypothesis",
                "claim": "Prioritize the critical timing path.",
                "timing_recipe_id": "tns_global",
            },
            "evaluation_error": None,
        },
    )
    atomic_json(
        first / "students" / "student_1" / "artifacts" / "evidence.json",
        {"state": "validated", "mechanism_fired": True},
    )
    atomic_json(
        first / "round.json",
        {
            "round": 1,
            "promoted_student": "student_1",
            "parent_after": {"parent_id": "round_001:student_1"},
            "results": [
                {
                    "student_id": "student_1",
                    "hypothesis_id": "timing_hypothesis",
                }
            ],
        },
    )

    second = root / "rounds" / "round_002"
    atomic_json(
        second / "teacher_plan.json",
        {
            "teacher_ok": True,
            "diagnosis": {"dominant_bottleneck": "dynamic_power_pw"},
            "hypotheses": [
                {
                    "student_id": "student_1",
                    "hypothesis_id": "follow_up",
                    "claim": "Investigate a bounded power edit.",
                    "source_hooks": ["src/rsz/RecoverPower.cc"],
                    "timing_recipe_id": "legacy_setup",
                }
            ],
        },
    )
    (second / "students" / "student_1" / "workspace" / "source").mkdir(
        parents=True
    )
    (second / "students" / "student_2").mkdir(parents=True)


def test_campaign_snapshot_exposes_ideas_progress_and_qor_history() -> None:
    from goalevolve.dashboard import campaign_snapshot

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "campaign"
        _write_campaign(root)
        snapshot = campaign_snapshot(root)

    assert snapshot["design"] == "demo_design"
    assert snapshot["campaign_status"] == "running"
    assert [row["round"] for row in snapshot["rounds"]] == [1, 2]
    assert snapshot["rounds"][0]["teacher"]["ideas"][0] == {
        "student_id": "student_1",
        "hypothesis_id": "timing_hypothesis",
        "claim": "Prioritize the critical timing path.",
        "source_hooks": ["src/rsz/RecoverTiming.cc"],
        "timing_recipe_id": "tns_global",
        "student_role": "explorer",
        "role_mode": "fresh_exploration",
        "epd_record_ids": [],
    }
    assert snapshot["rounds"][0]["students"][0]["status"] == "validated"
    assert snapshot["rounds"][1]["students"] == [
        {
            "student_id": "student_1",
            "status": "running",
            "hypothesis_id": "follow_up",
            "claim": "Investigate a bounded power edit.",
            "metrics": {},
            "checks": {},
            "evidence_state": None,
            "goal_distance": None,
        },
        {
            "student_id": "student_2",
            "status": "queued",
            "hypothesis_id": None,
            "claim": None,
            "metrics": {},
            "checks": {},
            "evidence_state": None,
            "goal_distance": None,
        },
    ]
    assert snapshot["qor_history"][0]["kind"] == "baseline"
    assert snapshot["qor_history"][1]["result_id"] == "round_001:student_1"
    assert snapshot["qor_history"][1]["goal_distance"] < snapshot["qor_history"][0]["goal_distance"]
    assert snapshot["top_results"][0]["result_id"] == "round_001:student_1"
    assert snapshot["top_results"][0]["evidence_state"] == "validated"


def test_campaign_snapshot_recovers_student_assignment_from_committed_round() -> None:
    from goalevolve.dashboard import campaign_snapshot

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "campaign"
        _write_campaign(root)
        plan = root / "rounds" / "round_001" / "teacher_plan.json"
        payload = json.loads(plan.read_text(encoding="utf-8"))
        payload["hypotheses"][0].pop("student_id")
        plan.write_text(json.dumps(payload), encoding="utf-8")
        snapshot = campaign_snapshot(root)

    assert snapshot["rounds"][0]["teacher"]["ideas"][0]["student_id"] == "student_1"


def test_campaign_snapshot_rejects_a_missing_campaign_root() -> None:
    from goalevolve.dashboard import campaign_snapshot

    with tempfile.TemporaryDirectory() as temporary:
        missing = Path(temporary) / "missing"
        try:
            campaign_snapshot(missing)
        except ValueError as error:
            assert str(missing) in str(error)
        else:
            raise AssertionError("campaign_snapshot accepted a missing state root")


def test_cli_exposes_dashboard_command() -> None:
    from goalevolve.cli import build_parser

    args = build_parser().parse_args(
        ["dashboard", "--state-root", "outputs/ae3/demo", "--port", "8091"]
    )

    assert args.command == "dashboard"
    assert args.state_root == "outputs/ae3/demo"
    assert args.port == 8091


def test_dashboard_http_server_serves_snapshot_and_assets() -> None:
    from goalevolve.dashboard import _handler

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "campaign"
        _write_campaign(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(root))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(f"{base}/api/dashboard") as response:
                snapshot = response.read().decode("utf-8")
                content_type = response.headers["Content-Type"]
            with urlopen(f"{base}/") as response:
                page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    assert content_type == "application/json; charset=utf-8"
    assert '"design":"demo_design"' in snapshot
    assert "GoalEvolve AE-3 Dashboard" in page
