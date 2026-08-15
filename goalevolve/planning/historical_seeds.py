"""Read-only historical mechanism descriptions for fresh campaign planning."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from .timing_recovery import timing_recipe


_ALLOWED_FIELDS = frozenset(
    {
        "seed_id",
        "source_anchors",
        "decision_boundary",
        "summary",
        "expected_signals",
        "activation_signals",
        "reference_diff_paths",
        "timing_recipe_id",
        "materialization_mode",
        "reference_parent_file_hashes",
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
_SCHEDULE_FIELDS = frozenset({"round_index", "seed_ids", "stages"})
_MATERIALIZATION_MODES = frozenset({"exact_reference_patch"})


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
    activation_signals = tuple(
        str(signal).strip()
        for signal in list(card.get("activation_signals") or signals)
        if str(signal).strip()
    )
    timing_recipe_id = str(card.get("timing_recipe_id") or "").strip()
    materialization_mode = str(card.get("materialization_mode") or "").strip()
    if timing_recipe_id:
        timing_recipe(timing_recipe_id)
    if materialization_mode and materialization_mode not in _MATERIALIZATION_MODES:
        raise ValueError(
            "unsupported historical seed materialization mode:"
            + materialization_mode
        )
    reference_diff_paths = tuple(
        str(Path(str(raw_path)).resolve())
        for raw_path in list(card.get("reference_diff_paths") or ())
        if str(raw_path).strip()
    )
    raw_reference_hashes = card.get("reference_parent_file_hashes")
    if raw_reference_hashes is None:
        reference_parent_file_hashes: dict[str, str] = {}
    elif isinstance(raw_reference_hashes, Mapping):
        reference_parent_file_hashes = {
            str(path): str(digest)
            for path, digest in raw_reference_hashes.items()
        }
    else:
        raise ValueError("historical seed reference parent file hashes must be an object")
    if not seed_id or not decision_boundary or not summary or not anchors:
        raise ValueError("historical seed has an empty required value")
    if signals and (
        not activation_signals or not set(activation_signals).issubset(signals)
    ):
        raise ValueError(
            "historical seed activation signals must be a nonempty subset of expected signals"
        )
    if any("::" not in anchor for anchor in anchors):
        raise ValueError("historical seed source anchors must use path::symbol form")
    for raw_path in reference_diff_paths:
        path = Path(raw_path)
        if path.suffix != ".diff" or not path.is_file():
            raise ValueError(f"historical seed reference diff is not a readable .diff file:{path}")
    if materialization_mode == "exact_reference_patch":
        if not reference_parent_file_hashes:
            raise ValueError(
                "exact reference materialization requires reference parent file hashes"
            )
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            for digest in reference_parent_file_hashes.values()
        ):
            raise ValueError("historical seed reference parent file hashes must be SHA-256")
    normalized = {
        "seed_id": seed_id,
        "source_anchors": anchors,
        "decision_boundary": decision_boundary,
        "summary": summary,
        "expected_signals": signals,
        "activation_signals": activation_signals,
        "reference_diff_paths": reference_diff_paths,
    }
    if timing_recipe_id:
        normalized["timing_recipe_id"] = timing_recipe_id
    if materialization_mode:
        normalized["materialization_mode"] = materialization_mode
        normalized["reference_parent_file_hashes"] = reference_parent_file_hashes
    return normalized


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


def load_historical_seed_revalidation_schedule(
    value: object,
    cards: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Validate a campaign-local sequence for parent-dependent seed cards.

    The card catalog remains a source-only historical reference.  A schedule
    merely decides which already-declared card is eligible in a particular
    round and stage; it cannot import historical QoR or promotion authority.
    Empty schedules retain the legacy first-round-only behavior.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("historical seed revalidation schedule must be a list")
    known_ids = {str(card.get("seed_id") or "") for card in cards}
    normalized: list[dict[str, object]] = []
    seen_rounds: set[int] = set()
    seen_seed_ids: set[str] = set()
    for raw_slot in value:
        if not isinstance(raw_slot, Mapping):
            raise ValueError("historical seed revalidation slot must be an object")
        unexpected = set(raw_slot).difference(_SCHEDULE_FIELDS)
        if unexpected:
            raise ValueError(
                "unexpected historical seed schedule fields:"
                + ",".join(sorted(unexpected))
            )
        raw_round = raw_slot.get("round_index")
        if isinstance(raw_round, bool):
            raise ValueError("historical seed schedule round_index must be positive")
        try:
            round_index = int(raw_round)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "historical seed schedule round_index must be positive"
            ) from error
        if round_index <= 0 or round_index in seen_rounds:
            raise ValueError("historical seed schedule rounds must be unique positive integers")
        raw_ids = raw_slot.get("seed_ids")
        if isinstance(raw_ids, str) or not isinstance(raw_ids, Sequence):
            raise ValueError("historical seed schedule seed_ids must be a nonempty list")
        seed_ids = tuple(str(seed_id).strip() for seed_id in raw_ids if str(seed_id).strip())
        if not seed_ids or len(set(seed_ids)) != len(seed_ids):
            raise ValueError("historical seed schedule seed_ids must be unique and nonempty")
        unknown = set(seed_ids).difference(known_ids)
        if unknown:
            raise ValueError(
                "historical seed schedule references unknown cards:"
                + ",".join(sorted(unknown))
            )
        if seen_seed_ids.intersection(seed_ids):
            raise ValueError("historical seed schedule may not reuse a seed card")
        raw_stages = raw_slot.get("stages")
        if isinstance(raw_stages, str) or not isinstance(raw_stages, Sequence):
            raise ValueError("historical seed schedule stages must be a nonempty list")
        stages = tuple(str(stage).strip() for stage in raw_stages if str(stage).strip())
        if not stages or len(set(stages)) != len(stages):
            raise ValueError("historical seed schedule stages must be unique and nonempty")
        normalized.append(
            {
                "round_index": round_index,
                "seed_ids": seed_ids,
                "stages": stages,
            }
        )
        seen_rounds.add(round_index)
        seen_seed_ids.update(seed_ids)
    return tuple(sorted(normalized, key=lambda slot: int(slot["round_index"])))


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


def source_resolvable_seeds(
    source_root: Path,
    seeds: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Resolve seed anchors from live source for the card-only planning path.

    The ``openroad_cards`` mode intentionally does not construct an AST graph.
    It nevertheless must not discard a configured historical source card just
    because graph metadata is absent.  This narrow textual check is only an
    availability filter; normal Controller assignment admission later checks
    every selected ``path::symbol`` against the same live parent.
    """

    root = Path(source_root)
    accepted: list[dict[str, object]] = []
    for seed in seeds:
        normalized = _normalize_card(seed)
        resolved = True
        for raw_anchor in tuple(str(anchor) for anchor in normalized["source_anchors"]):
            relative, separator, symbol = raw_anchor.partition("::")
            path = root / relative
            if not separator or not path.is_file():
                resolved = False
                break
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                resolved = False
                break
            declarator = symbol.split("(", 1)[0].strip()
            pieces = tuple(part for part in declarator.split("::") if part)
            candidates = (
                declarator,
                "::".join(pieces[-2:]),
                pieces[-1] if pieces else "",
            )
            if not any(candidate and candidate in text for candidate in candidates):
                resolved = False
                break
        if resolved:
            accepted.append(normalized)
    return tuple(accepted)
