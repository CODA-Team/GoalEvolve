"""Read-only historical mechanism descriptions for fresh campaign planning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence


_ALLOWED_FIELDS = frozenset(
    {
        "seed_id",
        "source_anchors",
        "decision_boundary",
        "summary",
        "expected_signals",
    }
)
_FORBIDDEN_AUTHORITY_FIELDS = frozenset(
    {
        "metrics",
        "parent_id",
        "source_commit",
        "source_hash",
        "promotion",
        "promoted_student",
        "goal_distance",
    }
)
_REQUIRED_FIELDS = frozenset(
    {
        "seed_id",
        "source_anchors",
        "decision_boundary",
        "summary",
        "expected_signals",
    }
)


def _normalize_card(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("historical seed must be an object")
    card = dict(value)
    for key in sorted(_FORBIDDEN_AUTHORITY_FIELDS.intersection(card)):
        raise ValueError(f"forbidden authority field:{key}")
    unexpected = set(card).difference(_ALLOWED_FIELDS)
    if unexpected:
        raise ValueError("unexpected historical seed fields:" + ",".join(sorted(unexpected)))
    missing = _REQUIRED_FIELDS.difference(card)
    if missing:
        raise ValueError("historical seed lacks:" + ",".join(sorted(missing)))
    seed_id = str(card["seed_id"]).strip()
    decision_boundary = str(card["decision_boundary"]).strip()
    summary = str(card["summary"]).strip()
    anchors = tuple(str(anchor).strip() for anchor in list(card["source_anchors"]) if str(anchor).strip())
    signals = tuple(str(signal).strip() for signal in list(card["expected_signals"]) if str(signal).strip())
    if not seed_id or not decision_boundary or not summary or not anchors:
        raise ValueError("historical seed has an empty required value")
    if any("::" not in anchor for anchor in anchors):
        raise ValueError("historical seed source anchors must use path::symbol form")
    return {
        "seed_id": seed_id,
        "source_anchors": anchors,
        "decision_boundary": decision_boundary,
        "summary": summary,
        "expected_signals": signals,
    }


def load_historical_seed_cards(path: Path | None) -> tuple[dict[str, object], ...]:
    """Load cards that inform a new experiment but can never carry QoR authority."""
    if path is None:
        return ()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        cards = payload.get("cards")
    else:
        cards = payload
    if not isinstance(cards, list):
        raise ValueError("historical seed catalog must contain a cards list")
    normalized = tuple(_normalize_card(card) for card in cards)
    identifiers = [str(card["seed_id"]) for card in normalized]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("historical seed identifiers must be unique")
    return normalized


def graph_resolvable_seeds(graph, seeds: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Return only cards whose every declared symbol resolves in this parent."""
    if graph is None:
        return ()
    accepted: list[dict[str, object]] = []
    for seed in seeds:
        normalized = _normalize_card(seed)
        anchors = tuple(str(anchor) for anchor in normalized["source_anchors"])
        if all(bool(getattr(graph.resolve_anchor(anchor), "resolved", False)) for anchor in anchors):
            accepted.append(normalized)
    return tuple(accepted)
