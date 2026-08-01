"""Read-only local dashboard for a persisted AE-3 campaign."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .core.contracts import GoalContract
from .core.io import load_json


STATIC_ROOT = Path(__file__).with_name("dashboard_static")
REQUIRED_CHECKS = ("build", "flow", "metrics", "lec")


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _payload(path: Path) -> dict[str, Any]:
    data = load_json(path, {})
    return dict(data) if isinstance(data, Mapping) else {}


def _round_number(path: Path) -> int | None:
    suffix = path.name.removeprefix("round_")
    return int(suffix) if suffix.isdigit() else None


def _round_roots(state_root: Path) -> list[Path]:
    rounds = state_root / "rounds"
    if not rounds.is_dir():
        return []
    return sorted(
        (path for path in rounds.iterdir() if path.is_dir() and _round_number(path) is not None),
        key=lambda path: int(_round_number(path) or 0),
    )


def _idea(raw: Mapping[str, Any]) -> dict[str, Any]:
    hooks = raw.get("source_hooks") or ()
    return {
        "student_id": str(raw.get("student_id") or ""),
        "hypothesis_id": str(raw.get("hypothesis_id") or ""),
        "claim": str(raw.get("claim") or ""),
        "source_hooks": [str(item) for item in hooks if item],
        "timing_recipe_id": str(raw.get("timing_recipe_id") or ""),
    }


def _checks(payload: Mapping[str, Any]) -> dict[str, bool]:
    return {
        str(row.get("name")): bool(row.get("passed"))
        for row in list(payload.get("checks") or ())
        if isinstance(row, Mapping) and row.get("name")
    }


def _candidate_status(
    candidate: Mapping[str, Any] | None,
    evidence: Mapping[str, Any],
    student_root: Path,
) -> str:
    if candidate:
        if candidate.get("evaluation_error"):
            return "failed"
        evidence_state = str(evidence.get("state") or "")
        if evidence_state:
            return evidence_state
        checks = _checks(candidate)
        if checks and not all(checks.get(name, False) for name in REQUIRED_CHECKS):
            return "evaluating"
        return "measured"
    if (student_root / "workspace").exists() or (student_root / "artifacts" / "codex").exists():
        return "running"
    return "queued"


def _candidate_metrics(payload: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(name): number
        for name, value in dict(payload.get("metrics") or {}).items()
        if (number := _number(value)) is not None
    }


def _goal_distance(contract: GoalContract, metrics: Mapping[str, Any]) -> float | None:
    distance, _, missing = contract.evaluate(metrics)
    return None if missing else distance


def _candidate_is_valid(candidate: Mapping[str, Any], metrics: Mapping[str, float]) -> bool:
    if candidate.get("evaluation_error") or _number(metrics.get("drv_count")) != 0.0:
        return False
    checks = _checks(candidate)
    return all(checks.get(name, False) for name in REQUIRED_CHECKS)


def _student_record(
    *,
    student_id: str,
    student_root: Path,
    idea: Mapping[str, Any],
    contract: GoalContract,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    artifacts = student_root / "artifacts"
    candidate_path = artifacts / "candidate.json"
    candidate = _payload(candidate_path) if candidate_path.is_file() else {}
    evidence = _payload(artifacts / "evidence.json") if candidate else {}
    hypothesis = dict(candidate.get("hypothesis") or {}) if candidate else {}
    if not hypothesis:
        hypothesis = dict(idea)
    metrics = _candidate_metrics(candidate)
    distance = _goal_distance(contract, metrics) if candidate else None
    record = {
        "student_id": student_id,
        "status": _candidate_status(candidate or None, evidence, student_root),
        "hypothesis_id": hypothesis.get("hypothesis_id") or None,
        "claim": hypothesis.get("claim") or None,
        "metrics": metrics,
        "checks": _checks(candidate),
        "evidence_state": evidence.get("state") or None,
        "goal_distance": distance,
    }
    if not candidate or distance is None:
        return record, None
    result = {
        "result_id": "",
        "round": 0,
        "student_id": student_id,
        "metrics": metrics,
        "goal_distance": distance,
        "valid": _candidate_is_valid(candidate, metrics),
        "evidence_state": evidence.get("state") or "unclassified",
        "status": record["status"],
    }
    return record, result


def _campaign_contract(state_root: Path) -> GoalContract:
    path = state_root / "contract.json"
    data = load_json(path)
    if not isinstance(data, Mapping):
        raise ValueError(f"campaign contract is missing: {path}")
    return GoalContract.from_dict(data)


def _round_snapshot(round_root: Path, contract: GoalContract) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = _payload(round_root / "teacher_plan.json")
    committed = _payload(round_root / "round.json")
    diagnosis = dict(plan.get("diagnosis") or committed.get("diagnosis") or {})
    assignments = {
        str(row.get("hypothesis_id")): str(row.get("student_id"))
        for row in list(committed.get("results") or ())
        if isinstance(row, Mapping) and row.get("hypothesis_id") and row.get("student_id")
    }
    ideas: list[dict[str, Any]] = []
    for raw in list(plan.get("hypotheses") or ()):
        if not isinstance(raw, Mapping):
            continue
        idea = _idea(raw)
        if not idea["student_id"]:
            idea["student_id"] = assignments.get(str(idea["hypothesis_id"]), "")
        ideas.append(idea)
    ideas_by_student = {str(item["student_id"]): item for item in ideas if item["student_id"]}
    students_root = round_root / "students"
    student_ids = set(ideas_by_student)
    if students_root.is_dir():
        student_ids.update(path.name for path in students_root.iterdir() if path.is_dir())

    students: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for student_id in sorted(student_ids):
        student_root = students_root / student_id
        record, result = _student_record(
            student_id=student_id,
            student_root=student_root,
            idea=ideas_by_student.get(student_id, {}),
            contract=contract,
        )
        students.append(record)
        if result is not None:
            result["round"] = int(_round_number(round_root) or 0)
            result["result_id"] = f"{round_root.name}:{student_id}"
            results.append(result)

    return {
        "round": int(_round_number(round_root) or 0),
        "round_id": round_root.name,
        "status": "completed" if committed else "running",
        "teacher": {
            "status": "completed" if committed else "planning",
            "ok": plan.get("teacher_ok"),
            "dominant_bottleneck": diagnosis.get("dominant_bottleneck") or None,
            "responsible_stage": diagnosis.get("responsible_stage") or None,
            "ideas": ideas,
            "promoted_student": committed.get("promoted_student") or None,
        },
        "students": students,
    }, results


def _baseline_row(contract: GoalContract) -> dict[str, Any]:
    distance, _, _ = contract.evaluate(contract.baseline_metrics)
    return {
        "result_id": "baseline",
        "kind": "baseline",
        "round": 0,
        "student_id": "p0",
        "metrics": dict(contract.baseline_metrics),
        "goal_distance": distance,
        "valid": True,
        "evidence_state": "baseline",
        "status": "baseline",
    }


def _frontier(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for row in history:
        if not row.get("valid"):
            continue
        if best is None or float(row["goal_distance"]) < float(best["goal_distance"]):
            best = row
        frontier.append({
            "round": row["round"],
            "result_id": best["result_id"],
            "goal_distance": best["goal_distance"],
            "metrics": best["metrics"],
        })
    return frontier


def campaign_snapshot(state_root: Path) -> dict[str, Any]:
    """Build a browser-safe, read-only view of one persisted AE-3 campaign."""
    state_root = Path(state_root).expanduser().resolve()
    if not state_root.is_dir():
        raise ValueError(f"campaign state root does not exist: {state_root}")
    contract = _campaign_contract(state_root)
    rounds: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for root in _round_roots(state_root):
        round_data, round_candidates = _round_snapshot(root, contract)
        rounds.append(round_data)
        candidates.extend(round_candidates)

    baseline = _baseline_row(contract)
    history = [baseline, *sorted(candidates, key=lambda row: (int(row["round"]), str(row["student_id"])))]
    valid_candidates = [row for row in candidates if row["valid"]]
    top_results = sorted(valid_candidates, key=lambda row: (float(row["goal_distance"]), int(row["round"]), str(row["student_id"])))[:3]
    if not top_results:
        top_results = [baseline]
    parent = _payload(state_root / "parent.json")
    active_round = any(round_data["status"] == "running" for round_data in rounds)
    return {
        "schema_version": "goalevolve.v2.dashboard.v1",
        "design": contract.design,
        "campaign": state_root.name,
        "campaign_status": "running" if active_round else "idle",
        "current_parent": {
            "parent_id": parent.get("parent_id") or None,
            "metrics": _candidate_metrics(parent),
            "goal_distance": _number(parent.get("goal_distance")),
        },
        "contract": {
            "contract_id": contract.contract_id,
            "metrics": [
                {
                    "name": metric.name,
                    "baseline": metric.baseline,
                    "target": metric.target,
                    "minimize": metric.minimize,
                }
                for metric in contract.metrics
            ],
        },
        "rounds": rounds,
        "qor_history": history,
        "qor_frontier": _frontier(history),
        "top_results": top_results,
    }


def _handler(state_root: Path):
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "GoalEvolveDashboard/1.0"

        def _send_json(self, payload: Mapping[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_asset(self, name: str, content_type: str) -> None:
            path = STATIC_ROOT / name
            if not path.is_file():
                self._send_json({"error": "dashboard asset is missing"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/api/dashboard":
                try:
                    self._send_json(campaign_snapshot(state_root))
                except ValueError as error:
                    self._send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            if path == "/healthz":
                self._send_json({"ok": True})
                return
            if path in {"/", "/index.html"}:
                self._send_asset("index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._send_asset("app.js", "text/javascript; charset=utf-8")
                return
            if path == "/style.css":
                self._send_asset("style.css", "text/css; charset=utf-8")
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[GoalEvolve][dashboard] {format % args}", flush=True)

    return DashboardHandler


def serve_dashboard(*, state_root: Path, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), _handler(Path(state_root).resolve()))
    print(f"GoalEvolve dashboard: http://{host}:{port}", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="serve the local GoalEvolve AE-3 dashboard")
    parser.add_argument("--state-root", required=True, help="AE-3 campaign state root")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--once", action="store_true", help="print one JSON snapshot and exit")
    args = parser.parse_args(argv)
    state_root = Path(args.state_root)
    if args.once:
        print(json.dumps(campaign_snapshot(state_root), ensure_ascii=False, indent=2))
        return 0
    serve_dashboard(state_root=state_root, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
