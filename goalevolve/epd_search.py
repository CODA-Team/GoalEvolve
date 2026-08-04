from __future__ import annotations

import argparse
import json
import re
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core.io import canonical_json, load_json


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", str(value or "").lower())
        if len(token) > 1
    }


def _as_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value if str(item))
    return ()


class EPDSearch:
    """Deterministic, path-oriented search over an EPD projection."""

    def __init__(self, state_root: Path) -> None:
        self.state_root = Path(state_root)
        self.root = self.state_root / "knowledge" / "epd"
        self.manifest = load_json(self.root / "manifest.json", {}) or {}

    def _jsonl(self, path: Path) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return rows
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows

    def _catalog(self) -> dict[str, dict[str, object]]:
        return {
            str(row.get("idea_id") or ""): row
            for row in self._jsonl(self.root / "indexes" / "idea_catalog.jsonl")
            if str(row.get("idea_id") or "")
        }

    def _corpus(self) -> list[dict[str, object]]:
        return self._jsonl(self.root / "indexes" / "retrieval_corpus.jsonl")

    @staticmethod
    def _query(value: Mapping[str, object]) -> dict[str, object]:
        return {
            "stage": str(value.get("stage") or ""),
            "source_hooks": _as_strings(value.get("source_hooks") or value.get("source_hook")),
            "decision_boundary": str(value.get("decision_boundary") or value.get("decision_type") or ""),
            "text": " ".join(
                str(value.get(name) or "")
                for name in (
                    "problem",
                    "observed_state",
                    "action",
                    "proposed_action",
                    "expected_effect",
                    "guard",
                )
            ).strip(),
        }

    @staticmethod
    def _trace(path: Path | None, payload: Mapping[str, object]) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(canonical_json({"timestamp": int(time.time()), **dict(payload)}) + "\n")

    def search(
        self,
        *,
        query: Mapping[str, object],
        stage: str = "",
        top_k: int = 8,
        trace_path: Path | None = None,
    ) -> list[dict[str, object]]:
        normalized = self._query(query)
        requested_stage = stage or str(normalized["stage"])
        query_hooks = set(normalized["source_hooks"])
        query_boundary = str(normalized["decision_boundary"])
        query_tokens = _tokens(normalized["text"] + " " + query_boundary)
        catalog = self._catalog()
        ranked: list[tuple[tuple[int, int, int, int, str], dict[str, object]]] = []
        for row in self._corpus():
            idea_id = str(row.get("idea_id") or "")
            if not idea_id:
                continue
            candidate_stage = str(row.get("stage") or "")
            candidate_hooks = set(_as_strings(row.get("source_hooks")))
            candidate_boundary = str(row.get("decision_boundary") or "")
            candidate_tokens = _tokens(row.get("text")) | _tokens(candidate_boundary)
            stage_match = int(bool(requested_stage) and candidate_stage == requested_stage)
            hook_match = len(query_hooks & candidate_hooks)
            boundary_match = int(bool(query_boundary) and candidate_boundary == query_boundary)
            overlap = len(query_tokens & candidate_tokens)
            card = catalog.get(idea_id, {})
            result = {
                "idea_id": idea_id,
                "idea_path": str(row.get("idea_path") or card.get("idea_path") or ""),
                "stage": candidate_stage,
                "source_hooks": sorted(candidate_hooks),
                "decision_boundary": candidate_boundary,
                "attempt_ids": list(card.get("attempt_ids") or ()),
                "score": {
                    "stage_match": stage_match,
                    "source_hook_matches": hook_match,
                    "decision_boundary_match": boundary_match,
                    "token_overlap": overlap,
                },
            }
            ranked.append(((-stage_match, -hook_match, -boundary_match, -overlap, idea_id), result))
        results = [row for _, row in sorted(ranked, key=lambda item: item[0])[: max(0, int(top_k))]]
        for rank, row in enumerate(results, start=1):
            row["rank"] = rank
        self._trace(
            trace_path,
            {
                "operation": "search",
                "query": dict(query),
                "stage": requested_stage,
                "top_k": int(top_k),
                "result_ids": [str(row["idea_id"]) for row in results],
                "opened_idea_ids": [],
            },
        )
        return results

    def show(
        self,
        *,
        idea_id: str,
        include: Sequence[str] = ("idea",),
        trace_path: Path | None = None,
    ) -> dict[str, object]:
        catalog = self._catalog()
        card = catalog.get(idea_id)
        if card is None:
            raise KeyError(f"unknown EPD idea: {idea_id}")
        requested = {str(item).strip().lower() for item in include}
        idea_path = Path(str(card.get("idea_path") or ""))
        payload: dict[str, object] = {"idea_id": idea_id}
        idea = load_json(idea_path, {}) or {}
        if "idea" in requested:
            payload["idea"] = idea
        attempts: list[dict[str, object]] = []
        for attempt_id in list(card.get("attempt_ids") or ()):
            attempt_path = self.root / "attempts" / str(attempt_id) / "attempt.json"
            attempt = load_json(attempt_path, {}) or {}
            if attempt:
                attempts.append(attempt)
        if "attempts" in requested:
            payload["attempts"] = attempts
        if "reflections" in requested:
            payload["reflections"] = [
                {
                    "attempt_id": str(row.get("record_id") or ""),
                    "path": str(self.root / "attempts" / str(row.get("record_id") or "") / "student_reflection.md"),
                    "text": (self.root / "attempts" / str(row.get("record_id") or "") / "student_reflection.md").read_text(encoding="utf-8"),
                }
                for row in attempts
                if (self.root / "attempts" / str(row.get("record_id") or "") / "student_reflection.md").is_file()
            ]
        self._trace(trace_path, {"operation": "show", "idea_id": idea_id, "include": sorted(requested), "opened_idea_ids": [idea_id]})
        return payload

    def compare(
        self,
        *,
        query: Mapping[str, object],
        idea_id: str,
        trace_path: Path | None = None,
    ) -> dict[str, object]:
        shown = self.show(idea_id=idea_id, include=("idea",), trace_path=None)
        idea = dict(shown.get("idea") or {})
        normalized = self._query(query)
        overlap: list[str] = []
        differences: list[str] = []
        comparisons = (
            ("stage", str(normalized["stage"]), str(idea.get("stage") or "")),
            ("decision_boundary", str(normalized["decision_boundary"]), str(idea.get("decision_boundary") or "")),
        )
        for name, left, right in comparisons:
            if left and left == right:
                overlap.append(name)
            elif left or right:
                differences.append(name)
        query_hooks = set(normalized["source_hooks"])
        idea_hooks = set(_as_strings(idea.get("source_hooks")))
        if query_hooks & idea_hooks:
            overlap.append("source_hook")
        elif query_hooks or idea_hooks:
            differences.append("source_hook")
        query_tokens = _tokens(normalized["text"])
        idea_tokens = _tokens(" ".join(str(idea.get(name) or "") for name in ("idea", "observed_state", "proposed_action")))
        if query_tokens & idea_tokens:
            overlap.append("mechanism_terms")
        elif query_tokens:
            differences.append("mechanism_terms")
        result = {
            "idea_id": idea_id,
            "semantic_overlap": sorted(set(overlap)),
            "material_difference": "none" if not differences else ", ".join(sorted(set(differences))),
        }
        self._trace(trace_path, {"operation": "compare", "idea_id": idea_id, "query": dict(query), "opened_idea_ids": [idea_id], **result})
        return result

    def compatible_pairs(
        self,
        *,
        parent: str,
        stage: str,
        top_k: int = 10,
        trace_path: Path | None = None,
    ) -> list[dict[str, object]]:
        cards = []
        for row in list(self.manifest.get("mechanisms") or ()):
            if not isinstance(row, Mapping) or str(row.get("status") or "") not in {"validated", "promising"}:
                continue
            card = load_json(Path(str(row.get("path") or "")), {}) or {}
            if stage and str(card.get("stage") or "") != stage:
                continue
            cards.append(card)
        pairs: list[dict[str, object]] = []
        for left, right in combinations(sorted(cards, key=lambda card: str(card.get("mechanism_id") or "")), 2):
            left_writes = set(_as_strings(left.get("source_write_set")))
            right_writes = set(_as_strings(right.get("source_write_set")))
            left_hooks = set(_as_strings(left.get("source_hooks")))
            right_hooks = set(_as_strings(right.get("source_hooks")))
            shared_writes = sorted(left_writes & right_writes)
            shared_hooks = sorted(left_hooks & right_hooks)
            if shared_writes:
                continue
            pairs.append(
                {
                    "parent": parent,
                    "mechanism_ids": [str(left.get("mechanism_id") or ""), str(right.get("mechanism_id") or "")],
                    "shared_source_hooks": shared_hooks,
                    "shared_source_write_set": shared_writes,
                    "compatible": True,
                }
            )
        selected = pairs[: max(0, int(top_k))]
        self._trace(trace_path, {"operation": "compatible-pairs", "parent": parent, "stage": stage, "top_k": int(top_k), "pair_count": len(selected), "opened_idea_ids": []})
        return selected


def _load_query(path: str) -> dict[str, object]:
    value = load_json(Path(path), None)
    if not isinstance(value, dict):
        raise ValueError(f"query file must contain one JSON object: {path}")
    return value


def _trace_path(args: argparse.Namespace) -> Path:
    if args.trace_path:
        return Path(args.trace_path)
    return Path(args.state_root) / "rounds" / "round_000" / "teacher_epd_retrieval_trace.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m goalevolve.epd_search")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--trace-path")
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search")
    search.add_argument("--query-file", required=True)
    search.add_argument("--stage", default="")
    search.add_argument("--top-k", type=int, default=8)
    show = commands.add_parser("show")
    show.add_argument("--idea-id", required=True)
    show.add_argument("--include", default="idea")
    compare = commands.add_parser("compare")
    compare.add_argument("--query-file", required=True)
    compare.add_argument("--idea-id", required=True)
    pairs = commands.add_parser("compatible-pairs")
    pairs.add_argument("--parent", required=True)
    pairs.add_argument("--stage", default="")
    pairs.add_argument("--top-k", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    search = EPDSearch(Path(args.state_root))
    trace = _trace_path(args)
    if args.command == "search":
        result: Any = search.search(query=_load_query(args.query_file), stage=args.stage, top_k=args.top_k, trace_path=trace)
    elif args.command == "show":
        result = search.show(idea_id=args.idea_id, include=tuple(item.strip() for item in args.include.split(",") if item.strip()), trace_path=trace)
    elif args.command == "compare":
        result = search.compare(query=_load_query(args.query_file), idea_id=args.idea_id, trace_path=trace)
    else:
        result = search.compatible_pairs(parent=args.parent, stage=args.stage, top_k=args.top_k, trace_path=trace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
