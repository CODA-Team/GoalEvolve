"""Round-level Codex token accounting for campaign audit and cost comparison.

The ledger is deliberately observer-only.  It consumes immutable per-turn
usage records emitted by ``PersistentCodexRunner`` after a Codex turn ends;
it is never read by planning, EPD, or promotion code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core.io import atomic_json, load_json


_USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _usage(payload: dict[str, Any]) -> dict[str, int]:
    values = {key: max(0, int(dict(payload.get("usage") or {}).get(key, 0) or 0)) for key in _USAGE_KEYS}
    values["uncached_input_tokens"] = max(0, values["input_tokens"] - values["cached_input_tokens"])
    values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
    return values


def _add(target: dict[str, int], source: dict[str, int]) -> None:
    for key in (*_USAGE_KEYS, "uncached_input_tokens", "total_tokens"):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))


def _empty_totals() -> dict[str, int]:
    return {key: 0 for key in (*_USAGE_KEYS, "uncached_input_tokens", "total_tokens")}


def record_round_token_usage(*, state_root: Path, round_root: Path, round_index: int) -> dict[str, object]:
    """Aggregate every completed/retried Codex turn in one round.

    Each turn record is immutable and has an attempt-specific filename, so a
    retry is accounted for rather than overwritten by the later success.
    """
    calls: list[dict[str, object]] = []
    totals, by_identity = _empty_totals(), {}
    for path in sorted(round_root.rglob("token_usage_attempt_*.json")):
        payload = load_json(path, {}) or {}
        if not isinstance(payload, dict):
            continue
        usage = _usage(payload)
        identity = str(payload.get("identity") or "unknown")
        row = {
            "identity": identity,
            "operation_id": str(payload.get("operation_id") or "unknown"),
            "attempt": int(payload.get("attempt") or 0),
            "completed": bool(payload.get("completed")),
            "returncode": payload.get("returncode"),
            "artifact": str(path),
            "usage": usage,
        }
        calls.append(row)
        _add(totals, usage)
        identity_totals = by_identity.setdefault(identity, _empty_totals())
        _add(identity_totals, usage)
    report: dict[str, object] = {
        "schema_version": "goalevolve.v2.token_usage_round.v1",
        "decision_role": "observer_only",
        "round": round_index,
        "call_count": len(calls),
        "totals": totals,
        "by_identity": by_identity,
        "calls": calls,
    }
    atomic_json(round_root / "token_usage.json", report)
    _update_campaign_ledger(state_root=state_root, report=report)
    return report


def _update_campaign_ledger(*, state_root: Path, report: dict[str, object]) -> None:
    path = state_root / "token_usage.json"
    payload = load_json(path, {"schema_version": "goalevolve.v2.token_usage_campaign.v1", "decision_role": "observer_only", "rounds": {}}) or {}
    rounds = dict(payload.get("rounds") or {})
    rounds[f"round_{int(report['round']):03d}"] = report
    totals = _empty_totals()
    for row in rounds.values():
        if isinstance(row, dict):
            _add(totals, {key: int(dict(row.get("totals") or {}).get(key, 0) or 0) for key in totals})
    atomic_json(path, {"schema_version": "goalevolve.v2.token_usage_campaign.v1", "decision_role": "observer_only", "rounds": rounds, "totals": totals})
