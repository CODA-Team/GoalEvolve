"""AST-backed, P0-rooted repository graphs for editable OpenROAD sources.

The graph only reports source facts.  It does not choose an evolution, run a
candidate, or alter the controller's promotion decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from goalevolve.core.io import atomic_json, load_json, sha256_file, sha256_json

try:  # Import lazily enough to give a useful error on an unprepared host.
    from tree_sitter import Language, Parser
    import tree_sitter_cpp
except ImportError:  # pragma: no cover - covered by deployment, not unit tests
    Language = None  # type: ignore[assignment,misc]
    Parser = None  # type: ignore[assignment,misc]
    tree_sitter_cpp = None  # type: ignore[assignment]


GRAPH_SCHEMA_VERSION = "goalevolve.repository_graph.v6"
_CPP_SUFFIXES = frozenset({".cc", ".cpp", ".cxx", ".hh", ".hpp", ".h"})
_EDITABLE_SUBTREES = ("src/rsz", "src/rmp")
_METRIC_SIGNAL_RE = re.compile(r"METRIC\|([A-Za-z0-9_]+)")
_CALL_NAME_RE = re.compile(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*")


class RepositoryGraphUnavailable(RuntimeError):
    """The required tree-sitter Python runtime is unavailable."""


@dataclass(frozen=True)
class GraphSymbol:
    """A source-level class, struct, function, or method fact."""

    symbol_id: str
    path: str
    kind: str
    name: str
    qualified_name: str
    signature: str
    line_start: int
    line_end: int
    byte_start: int
    byte_end: int
    container: str | None
    source_digest: str
    declarator: str = ""
    call_names: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    unresolved_calls: tuple[str, ...] = ()
    metric_signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GraphSymbol":
        value = dict(payload)
        value.setdefault("declarator", "")
        for field in ("call_names", "calls", "unresolved_calls", "metric_signals"):
            value[field] = tuple(value.get(field, ()))
        return cls(**value)


@dataclass(frozen=True)
class GraphFile:
    """An editable C++ file and facts that came directly from its AST."""

    node_id: str
    path: str
    digest: str
    includes: tuple[str, ...]
    symbol_ids: tuple[str, ...]
    parse_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GraphFile":
        value = dict(payload)
        value["includes"] = tuple(value.get("includes", ()))
        value["symbol_ids"] = tuple(value.get("symbol_ids", ()))
        return cls(**value)


@dataclass(frozen=True)
class GraphEdge:
    """A deterministic relation between two graph nodes."""

    source: str
    target: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GraphEdge":
        return cls(**dict(payload))


@dataclass(frozen=True)
class AnchorResolution:
    """Exact source-anchor resolution used for controller admission."""

    anchor: str
    status: str
    symbols: tuple[GraphSymbol, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.status == "resolved"


@dataclass(frozen=True)
class RepositoryGraph:
    """The complete graph for P0 or a P0-derived parent snapshot."""

    source_hash: str
    base_source_hash: str
    allowed_patch_roots: tuple[str, ...]
    artifact_root: Path
    files: Mapping[str, GraphFile]
    symbols: Mapping[str, GraphSymbol]
    edges: tuple[GraphEdge, ...]
    reused_files: tuple[str, ...] = ()
    reparsed_files: tuple[str, ...] = ()

    def resolve_anchor(self, anchor: str) -> AnchorResolution:
        path, separator, requested = anchor.partition("::")
        if not separator or not path or not requested:
            return AnchorResolution(anchor=anchor, status="missing")
        if "(" in requested:
            normalized_requested = _normalized_pointer_spacing(requested)
            matches = [
                symbol
                for symbol in self.symbols.values()
                if symbol.path == path
                and (
                    _normalized_pointer_spacing(symbol.declarator) == normalized_requested
                    or _normalized_pointer_spacing(_qualified_declarator(symbol))
                    == normalized_requested
                )
            ]
        else:
            matches = [
                symbol
                for symbol in self.symbols.values()
                if symbol.path == path
                and (
                    symbol.qualified_name == requested
                    or symbol.qualified_name.endswith(f"::{requested}")
                    or symbol.name == requested
                )
            ]
        matches.sort(key=lambda item: (item.path, item.qualified_name, item.line_start))
        if len(matches) == 1:
            return AnchorResolution(anchor=anchor, status="resolved", symbols=tuple(matches))
        if matches:
            return AnchorResolution(anchor=anchor, status="ambiguous", symbols=tuple(matches))
        return AnchorResolution(anchor=anchor, status="missing")

    def symbol_for_anchor(self, anchor: str) -> GraphSymbol | None:
        resolution = self.resolve_anchor(anchor)
        return resolution.symbols[0] if resolution.resolved else None

    def edges_of_kind(self, kind: str) -> tuple[tuple[str, str], ...]:
        return tuple((edge.source, edge.target) for edge in self.edges if edge.kind == kind)

    def compact_index(self) -> dict[str, list[str]]:
        """Return a stable legacy-friendly compact view of AST symbols."""

        result: dict[str, list[str]] = {}
        for symbol in sorted(self.symbols.values(), key=lambda item: (item.path, item.line_start)):
            result.setdefault(symbol.path, []).append(symbol.qualified_name)
        return result

    def doc_cards(self) -> dict[str, dict[str, Any]]:
        """Generate a provenance card for every file and symbol node."""

        callers: dict[str, list[str]] = {}
        callees: dict[str, list[str]] = {}
        includes: dict[str, list[str]] = {}
        included_by: dict[str, list[str]] = {}
        contains: dict[str, list[str]] = {}
        for edge in self.edges:
            if edge.kind == "calls":
                callers.setdefault(edge.target, []).append(edge.source)
                callees.setdefault(edge.source, []).append(edge.target)
            elif edge.kind == "includes":
                includes.setdefault(edge.source, []).append(edge.target)
                included_by.setdefault(edge.target, []).append(edge.source)
            elif edge.kind == "contains":
                contains.setdefault(edge.source, []).append(edge.target)

        cards: dict[str, dict[str, Any]] = {}
        for path, source_file in sorted(self.files.items()):
            cards[source_file.node_id] = {
                "card_id": source_file.node_id,
                "node_kind": "file",
                "source_hash": self.source_hash,
                "path": path,
                "digest": source_file.digest,
                "parse_error": source_file.parse_error,
                "contains": sorted(contains.get(source_file.node_id, ())),
                "includes": sorted(includes.get(source_file.node_id, ())),
                "included_by": sorted(included_by.get(source_file.node_id, ())),
            }
        for symbol_id, symbol in sorted(self.symbols.items()):
            cards[symbol_id] = {
                "card_id": symbol_id,
                "node_kind": symbol.kind,
                "source_hash": self.source_hash,
                "path": symbol.path,
                "qualified_name": symbol.qualified_name,
                "signature": symbol.signature,
                "declarator": symbol.declarator,
                "line_start": symbol.line_start,
                "line_end": symbol.line_end,
                "container": symbol.container,
                "source_digest": symbol.source_digest,
                "metric_signals": list(symbol.metric_signals),
                "calls": sorted(callees.get(symbol_id, ())),
                "called_by": sorted(callers.get(symbol_id, ())),
                "unresolved_calls": list(symbol.unresolved_calls),
            }
        return cards

    def doc_card(self, anchor: str) -> dict[str, Any] | None:
        symbol = self.symbol_for_anchor(anchor)
        return self.doc_cards().get(symbol.symbol_id) if symbol is not None else None

    def focus(
        self,
        *,
        anchor_hints: Sequence[str] = (),
        metric_hints: Sequence[str] = (),
        allowed_patch_roots: Sequence[str] = (),
        max_cards: int = 24,
    ) -> dict[str, Any]:
        """Return a bounded, source-only card packet for a Teacher prompt."""

        explicit_ids = {
            resolution.symbols[0].symbol_id
            for hint in anchor_hints
            if (resolution := self.resolve_anchor(hint)).resolved
        }
        tokens = tuple(
            token.lower() for token in (*anchor_hints, *metric_hints) if token.strip()
        )

        def score(symbol: GraphSymbol) -> tuple[int, str, int]:
            source_text = " ".join(
                (symbol.path, symbol.qualified_name, symbol.signature, *symbol.metric_signals)
            ).lower()
            return (
                (100 if symbol.symbol_id in explicit_ids else 0)
                + sum(10 for token in tokens if token in source_text)
                + len(symbol.metric_signals),
                symbol.path,
                symbol.line_start,
            )

        allowed = tuple(root.strip("/") for root in allowed_patch_roots if root.strip("/"))
        scoped_symbols = [
            symbol
            for symbol in self.symbols.values()
            if not allowed or any(symbol.path == root or symbol.path.startswith(f"{root}/") for root in allowed)
        ]
        ranked = sorted(scoped_symbols, key=score, reverse=True)
        symbol_limit = max(1, int(max_cards))
        # Preserve a bounded semantic neighbourhood around the highest-ranked
        # anchors.  This keeps the prompt small while retaining the direct
        # caller/callee boundary needed to reason about a source-local change.
        seed_limit = min(len(ranked), max(1, (symbol_limit + 1) // 2))
        selected = list(ranked[:seed_limit])
        selected_ids = {symbol.symbol_id for symbol in selected}
        scoped_ids = {symbol.symbol_id for symbol in scoped_symbols}
        call_neighbors: list[GraphSymbol] = []
        for edge in self.edges:
            if edge.kind != "calls":
                continue
            neighbor_id = (
                edge.target if edge.source in selected_ids else edge.source if edge.target in selected_ids else ""
            )
            if not neighbor_id or neighbor_id not in scoped_ids or neighbor_id in selected_ids:
                continue
            neighbor = self.symbols.get(neighbor_id)
            if neighbor is not None:
                call_neighbors.append(neighbor)
        for neighbor in sorted(
            {item.symbol_id: item for item in call_neighbors}.values(),
            key=score,
            reverse=True,
        ):
            if len(selected) >= symbol_limit:
                break
            selected.append(neighbor)
            selected_ids.add(neighbor.symbol_id)
        one_hop_call_neighbor_count = len(selected) - seed_limit
        for symbol in ranked:
            if len(selected) >= symbol_limit:
                break
            if symbol.symbol_id not in selected_ids:
                selected.append(symbol)
                selected_ids.add(symbol.symbol_id)
        card_ids = {symbol.symbol_id for symbol in selected}
        card_ids.update(self.files[symbol.path].node_id for symbol in selected)
        cards = self.doc_cards()
        relation_fields = ("contains", "includes", "included_by", "calls", "called_by")
        packet_cards: list[dict[str, Any]] = []
        for card_id in sorted(card_ids):
            card = dict(cards[card_id])
            for field in relation_fields:
                if field in card:
                    card[field] = [target for target in card[field] if target in card_ids]
            packet_cards.append(card)
        induced_edges = [
            edge.to_dict()
            for edge in self.edges
            if edge.source in card_ids and edge.target in card_ids
        ]
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "source_hash": self.source_hash,
            "base_source_hash": self.base_source_hash,
            "allowed_patch_roots": list(allowed or self.allowed_patch_roots),
            "indexed_files": len(self.files),
            "indexed_symbols": len(self.symbols),
            "artifact_root": str(self.artifact_root),
            "reused_files": list(self.reused_files),
            "reparsed_files": list(self.reparsed_files),
            "simplification": {
                "max_symbol_cards": max_cards,
                "seed_symbol_count": seed_limit,
                "one_hop_call_neighbor_count": one_hop_call_neighbor_count,
                "backfill_symbol_count": len(selected) - seed_limit - one_hop_call_neighbor_count,
                "selected_symbol_count": len(selected),
                "omitted_symbol_count": max(0, len(scoped_symbols) - len(selected)),
                "selected_file_count": sum(card_id.startswith("file:") for card_id in card_ids),
                "induced_edge_count": len(induced_edges),
            },
            "cards": packet_cards,
            "edges": induced_edges,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "source_hash": self.source_hash,
            "base_source_hash": self.base_source_hash,
            "allowed_patch_roots": list(self.allowed_patch_roots),
            "files": [item.to_dict() for _, item in sorted(self.files.items())],
            "symbols": [item.to_dict() for _, item in sorted(self.symbols.items())],
            "edges": [item.to_dict() for item in self.edges],
            "reused_files": list(self.reused_files),
            "reparsed_files": list(self.reparsed_files),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, artifact_root: Path) -> "RepositoryGraph":
        files = {item["path"]: GraphFile.from_dict(item) for item in payload.get("files", ())}
        symbols = {item["symbol_id"]: GraphSymbol.from_dict(item) for item in payload.get("symbols", ())}
        return cls(
            source_hash=str(payload["source_hash"]),
            base_source_hash=str(payload.get("base_source_hash") or payload["source_hash"]),
            allowed_patch_roots=tuple(payload.get("allowed_patch_roots", ())),
            artifact_root=artifact_root,
            files=files,
            symbols=symbols,
            edges=tuple(GraphEdge.from_dict(item) for item in payload.get("edges", ())),
            reused_files=tuple(payload.get("reused_files", ())),
            reparsed_files=tuple(payload.get("reparsed_files", ())),
        )


class RepositoryGraphIndex:
    """Build P0 once, then derive source-hash-specific parent graphs from it."""

    def __init__(
        self,
        *,
        state_root: Path,
        p0_source_root: Path | None = None,
        p0_artifact_root: Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.state_root = Path(state_root)
        self.p0_source_root = Path(
            p0_source_root
            or project_root / "artifact_evaluation" / "lineage" / "openroad_power" / "p0" / "source"
        ).resolve()
        self.p0_artifact_root = Path(
            p0_artifact_root
            or self.p0_source_root.parent / "repository_graph"
        ).resolve()
        self._parser: Parser | None = None

    @property
    def parent_artifact_root(self) -> Path:
        return self.state_root / "knowledge" / "repository_graph"

    def build_p0(
        self,
        *,
        source_hash: str | None = None,
        allowed_patch_roots: Sequence[str] = _EDITABLE_SUBTREES,
    ) -> RepositoryGraph:
        """Build or load the immutable graph for the project-owned P0 snapshot."""

        if not self.p0_source_root.is_dir():
            raise FileNotFoundError(f"GoalEvolve P0 source snapshot is missing: {self.p0_source_root}")
        p0_hash = source_hash or self._p0_content_hash()
        # P0 is a project-level source map for both evolution modules.  A
        # design's narrower patch fence is enforced by the controller later;
        # it must not erase the other module from the shared baseline graph.
        roots = _editable_roots(self.p0_source_root, _EDITABLE_SUBTREES)
        cached = self._load_graph(self.p0_artifact_root)
        if self._compatible(cached, source_hash=p0_hash, base_source_hash=p0_hash, roots=roots):
            return cached  # type: ignore[return-value]
        return self._build(
            source_root=self.p0_source_root,
            source_hash=p0_hash,
            base_source_hash=p0_hash,
            roots=roots,
            artifact_root=self.p0_artifact_root,
            prior_graphs=(),
        )

    def build_parent(
        self,
        *,
        source_root: Path,
        source_hash: str,
        allowed_patch_roots: Sequence[str],
    ) -> RepositoryGraph:
        """Build a current-parent graph, reusing P0 facts and prior deltas."""

        roots = _editable_roots(Path(source_root), _EDITABLE_SUBTREES)
        p0 = self.build_p0()
        artifact_root = self.parent_artifact_root / source_hash
        existing = self._load_graph(artifact_root)
        if self._compatible(
            existing,
            source_hash=source_hash,
            base_source_hash=p0.source_hash,
            roots=roots,
        ):
            return existing  # type: ignore[return-value]
        return self._build(
            source_root=Path(source_root).resolve(),
            source_hash=source_hash,
            base_source_hash=p0.source_hash,
            roots=roots,
            artifact_root=artifact_root,
            prior_graphs=self._prior_graphs(p0=p0, roots=roots),
        )

    def build(
        self,
        *,
        source_root: Path,
        source_hash: str,
        allowed_patch_roots: Sequence[str],
    ) -> RepositoryGraph:
        """Compatibility entry point for callers that need a parent graph."""

        return self.build_parent(
            source_root=source_root,
            source_hash=source_hash,
            allowed_patch_roots=allowed_patch_roots,
        )

    def _build(
        self,
        *,
        source_root: Path,
        source_hash: str,
        base_source_hash: str,
        roots: tuple[str, ...],
        artifact_root: Path,
        prior_graphs: Sequence[RepositoryGraph],
    ) -> RepositoryGraph:
        digests = {
            path: sha256_file(source_root / path)
            for path in _source_paths(source_root, roots)
        }
        files: dict[str, GraphFile] = {}
        symbols: dict[str, GraphSymbol] = {}
        reused_files: list[str] = []
        reparsed_files: list[str] = []
        for path, digest in sorted(digests.items()):
            cached = _matching_file(path=path, digest=digest, graphs=prior_graphs)
            if cached is not None:
                source_file, cached_symbols = cached
                files[path] = source_file
                symbols.update({symbol.symbol_id: symbol for symbol in cached_symbols})
                reused_files.append(path)
                continue
            source_file, parsed_symbols = self._parse_file(
                source_root=source_root,
                path=path,
                digest=digest,
            )
            files[path] = source_file
            symbols.update({symbol.symbol_id: symbol for symbol in parsed_symbols})
            reparsed_files.append(path)

        edges, resolved_symbols = _build_edges(files=files, symbols=symbols)
        graph = RepositoryGraph(
            source_hash=source_hash,
            base_source_hash=base_source_hash,
            allowed_patch_roots=roots,
            artifact_root=artifact_root,
            files=files,
            symbols=resolved_symbols,
            edges=edges,
            reused_files=tuple(sorted(reused_files)),
            reparsed_files=tuple(sorted(reparsed_files)),
        )
        self._persist(graph)
        return graph

    def _parse_file(
        self,
        *,
        source_root: Path,
        path: str,
        digest: str,
    ) -> tuple[GraphFile, tuple[GraphSymbol, ...]]:
        raw = (source_root / path).read_bytes()
        tree = self._get_parser().parse(raw)
        symbols, includes = _extract_file_facts(raw=raw, path=path, digest=digest, tree=tree)
        return (
            GraphFile(
                node_id=_file_id(path),
                path=path,
                digest=digest,
                includes=includes,
                symbol_ids=tuple(symbol.symbol_id for symbol in symbols),
                parse_error=tree.root_node.has_error,
            ),
            symbols,
        )

    def _get_parser(self) -> Parser:
        if self._parser is not None:
            return self._parser
        if Language is None or Parser is None or tree_sitter_cpp is None:
            raise RepositoryGraphUnavailable(
                "Repository graphing requires tree-sitter and tree-sitter-cpp. "
                "Install GoalEvolve's Python dependencies before Codex planning."
            )
        self._parser = Parser(Language(tree_sitter_cpp.language()))
        return self._parser

    def _prior_graphs(
        self,
        *,
        p0: RepositoryGraph,
        roots: tuple[str, ...],
    ) -> tuple[RepositoryGraph, ...]:
        result = [p0]
        if self.parent_artifact_root.is_dir():
            candidates = sorted(
                self.parent_artifact_root.glob("*/graph.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            for path in candidates:
                graph = self._load_graph(path.parent)
                if graph is not None and graph.allowed_patch_roots == roots:
                    result.append(graph)
        return tuple(result)

    def _p0_content_hash(self) -> str:
        manifest = load_json(self.p0_source_root.parent / "source_manifest.json", {})
        digest = str((manifest or {}).get("content_sha256") or "")
        if digest:
            return digest
        return sha256_json(
            [
                (path, sha256_file(self.p0_source_root / path))
                for path in _source_paths(self.p0_source_root, _EDITABLE_SUBTREES)
            ]
        )

    @staticmethod
    def _compatible(
        graph: RepositoryGraph | None,
        *,
        source_hash: str,
        base_source_hash: str,
        roots: tuple[str, ...],
    ) -> bool:
        return bool(
            graph is not None
            and graph.source_hash == source_hash
            and graph.base_source_hash == base_source_hash
            and graph.allowed_patch_roots == roots
        )

    @staticmethod
    def _load_graph(artifact_root: Path) -> RepositoryGraph | None:
        payload = load_json(artifact_root / "graph.json")
        if not isinstance(payload, Mapping) or payload.get("schema_version") != GRAPH_SCHEMA_VERSION:
            return None
        try:
            return RepositoryGraph.from_dict(payload, artifact_root=artifact_root)
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _persist(graph: RepositoryGraph) -> None:
        graph.artifact_root.mkdir(parents=True, exist_ok=True)
        graph_payload = graph.to_dict()
        atomic_json(graph.artifact_root / "graph.json", graph_payload)
        atomic_json(
            graph.artifact_root / "doc_cards.json",
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "source_hash": graph.source_hash,
                "base_source_hash": graph.base_source_hash,
                "cards": graph.doc_cards(),
            },
        )
        atomic_json(
            graph.artifact_root / "manifest.json",
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "source_hash": graph.source_hash,
                "base_source_hash": graph.base_source_hash,
                "allowed_patch_roots": list(graph.allowed_patch_roots),
                "graph_digest": sha256_json(graph_payload),
                "files": {
                    path: {
                        "digest": source_file.digest,
                        "parse_error": source_file.parse_error,
                    }
                    for path, source_file in sorted(graph.files.items())
                },
                "reused_files": list(graph.reused_files),
                "reparsed_files": list(graph.reparsed_files),
            },
        )


def _editable_roots(source_root: Path, roots: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for configured in roots:
        root = configured.strip().strip("/")
        if not root:
            continue
        candidate = next(
            (
                editable
                if editable.startswith(f"{root}/")
                else root
                for editable in _EDITABLE_SUBTREES
                if root == editable
                or root.startswith(f"{editable}/")
                or editable.startswith(f"{root}/")
            ),
            None,
        )
        if candidate and (source_root / candidate).is_dir() and candidate not in normalized:
            normalized.append(candidate)
    return tuple(sorted(normalized))


def _source_paths(source_root: Path, roots: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    ignored_parts = frozenset({"test", "tests", "third-party", "build", "build_power", "__pycache__"})
    for root in roots:
        for path in sorted((source_root / root).rglob("*")):
            relative = path.relative_to(source_root)
            if path.is_file() and path.suffix in _CPP_SUFFIXES and not ignored_parts.intersection(relative.parts):
                result.append(relative.as_posix())
    return tuple(sorted(set(result)))


def _matching_file(
    *,
    path: str,
    digest: str,
    graphs: Sequence[RepositoryGraph],
) -> tuple[GraphFile, tuple[GraphSymbol, ...]] | None:
    for graph in graphs:
        source_file = graph.files.get(path)
        if source_file is None or source_file.digest != digest:
            continue
        symbols = tuple(
            graph.symbols[symbol_id]
            for symbol_id in source_file.symbol_ids
            if symbol_id in graph.symbols
        )
        if len(symbols) == len(source_file.symbol_ids):
            return source_file, symbols
    return None


def _extract_file_facts(
    *,
    raw: bytes,
    path: str,
    digest: str,
    tree: Any,
) -> tuple[tuple[GraphSymbol, ...], tuple[str, ...]]:
    includes: set[str] = set()
    symbols: list[GraphSymbol] = []

    def visit(node: Any, namespaces: tuple[str, ...], containers: tuple[str, ...]) -> None:
        if node.type == "preproc_include":
            include = _include_name(node=node, raw=raw)
            if include:
                includes.add(include)
        if node.type == "namespace_definition":
            name = _first_named_text(node=node, raw=raw, kinds={"namespace_identifier", "identifier"})
            next_namespaces = namespaces + tuple(part for part in name.split("::") if part) if name else namespaces
            for child in node.named_children:
                visit(child, next_namespaces, containers)
            return
        if node.type in {"class_specifier", "struct_specifier"}:
            name = _first_named_text(node=node, raw=raw, kinds={"type_identifier", "identifier"})
            next_containers = containers
            if name:
                qualified = "::".join((*namespaces, *containers, name))
                symbols.append(
                    _make_symbol(
                        raw=raw,
                        node=node,
                        path=path,
                        digest=digest,
                        kind="class" if node.type == "class_specifier" else "struct",
                        name=name,
                        qualified_name=qualified,
                        container="::".join((*namespaces, *containers)) or None,
                        call_names=(),
                    )
                )
                next_containers = containers + (name,)
            for child in node.named_children:
                visit(child, namespaces, next_containers)
            return
        if node.type == "function_definition":
            name, qualified_name, declarator = _function_names(
                node=node,
                raw=raw,
                namespaces=namespaces,
                containers=containers,
            )
            if name:
                prefix = _declarator_prefix(node, raw)
                symbols.append(
                    _make_symbol(
                        raw=raw,
                        node=node,
                        path=path,
                        digest=digest,
                        kind="method" if containers or "::" in prefix else "function",
                        name=name,
                        qualified_name=qualified_name,
                        container="::".join((*namespaces, *containers)) or None,
                        declarator=declarator,
                        call_names=_call_names(node=node, raw=raw),
                    )
                )
        for child in node.named_children:
            visit(child, namespaces, containers)

    visit(tree.root_node, (), ())
    return tuple(symbols), tuple(sorted(includes))


def _make_symbol(
    *,
    raw: bytes,
    node: Any,
    path: str,
    digest: str,
    kind: str,
    name: str,
    qualified_name: str,
    container: str | None,
    declarator: str = "",
    call_names: Iterable[str] = (),
) -> GraphSymbol:
    line_start = node.start_point[0] + 1
    line_end = node.end_point[0] + 1
    return GraphSymbol(
        symbol_id=_symbol_id(path, qualified_name, declarator, kind, line_start, digest),
        path=path,
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        signature=_signature(node=node, raw=raw),
        line_start=line_start,
        line_end=line_end,
        byte_start=node.start_byte,
        byte_end=node.end_byte,
        container=container,
        source_digest=digest,
        declarator=declarator,
        call_names=tuple(sorted(set(call_names))),
        metric_signals=tuple(sorted(set(_METRIC_SIGNAL_RE.findall(_node_text(node, raw))))),
    )


def _build_edges(
    *,
    files: Mapping[str, GraphFile],
    symbols: Mapping[str, GraphSymbol],
) -> tuple[tuple[GraphEdge, ...], dict[str, GraphSymbol]]:
    edges: set[GraphEdge] = set()
    for path, source_file in files.items():
        for symbol_id in source_file.symbol_ids:
            edges.add(GraphEdge(source=source_file.node_id, target=symbol_id, kind="contains"))
        for include in source_file.includes:
            target = _resolve_include(path=path, include=include, files=files)
            if target:
                edges.add(GraphEdge(source=source_file.node_id, target=files[target].node_id, kind="includes"))

    by_qualified: dict[str, list[GraphSymbol]] = {}
    by_short_name: dict[str, list[GraphSymbol]] = {}
    for symbol in symbols.values():
        by_qualified.setdefault(symbol.qualified_name, []).append(symbol)
        by_short_name.setdefault(symbol.name, []).append(symbol)

    resolved: dict[str, GraphSymbol] = {}
    for symbol_id, symbol in symbols.items():
        calls: list[str] = []
        unresolved: list[str] = []
        for call_name in symbol.call_names:
            target = _resolve_call(
                call_name=call_name,
                source=symbol,
                by_qualified=by_qualified,
                by_short_name=by_short_name,
            )
            if target is None:
                unresolved.append(call_name)
            else:
                calls.append(target.symbol_id)
                edges.add(GraphEdge(source=symbol_id, target=target.symbol_id, kind="calls"))
        resolved[symbol_id] = replace(
            symbol,
            calls=tuple(sorted(set(calls))),
            unresolved_calls=tuple(sorted(set(unresolved))),
        )
    return tuple(sorted(edges, key=lambda item: (item.kind, item.source, item.target))), resolved


def _resolve_include(*, path: str, include: str, files: Mapping[str, GraphFile]) -> str | None:
    direct = (Path(path).parent / include).as_posix()
    if direct in files:
        return direct
    if include in files:
        return include
    matches = [candidate for candidate in files if candidate.endswith(f"/{include}")]
    return matches[0] if len(matches) == 1 else None


def _resolve_call(
    *,
    call_name: str,
    source: GraphSymbol,
    by_qualified: Mapping[str, list[GraphSymbol]],
    by_short_name: Mapping[str, list[GraphSymbol]],
) -> GraphSymbol | None:
    candidates: list[GraphSymbol] = []
    if "::" in call_name:
        candidates.extend(by_qualified.get(call_name.lstrip(":"), ()))
        context = source.qualified_name.split("::")[:-1]
        if context:
            candidates.extend(by_qualified.get("::".join((*context, call_name)), ()))
    else:
        context = source.qualified_name.split("::")[:-1]
        while context:
            candidates.extend(by_qualified.get("::".join((*context, call_name)), ()))
            context.pop()
        candidates.extend(by_short_name.get(call_name, ()))
    unique = {candidate.symbol_id: candidate for candidate in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _include_name(*, node: Any, raw: bytes) -> str | None:
    for descendant in _walk(node):
        if descendant.type in {"string_literal", "system_lib_string"}:
            return _node_text(descendant, raw).strip().strip('"<>')
    return None


def _first_named_text(*, node: Any, raw: bytes, kinds: set[str]) -> str:
    for descendant in _walk(node):
        if descendant.type in kinds:
            return _node_text(descendant, raw).strip()
    return ""


def _function_names(
    *,
    node: Any,
    raw: bytes,
    namespaces: tuple[str, ...],
    containers: tuple[str, ...],
) -> tuple[str, str, str]:
    declarator = _function_declarator(node, raw)
    prefix = re.sub(r"\s+", "", declarator.split("(", 1)[0])
    prefix = re.sub(r"<[^<>]*>", "", prefix).lstrip(":")
    if not prefix:
        return "", "", ""
    name = prefix.split("::")[-1]
    if "::" in prefix:
        namespace_prefix = "::".join(namespaces)
        qualified = prefix if not namespace_prefix or prefix.startswith(f"{namespace_prefix}::") else f"{namespace_prefix}::{prefix}"
    else:
        qualified = "::".join((*namespaces, *containers, name))
    return name, qualified, declarator


def _declarator_prefix(node: Any, raw: bytes) -> str:
    return _function_declarator(node, raw).split("(", 1)[0].strip()


def _function_declarator(node: Any, raw: bytes) -> str:
    for descendant in _walk(node):
        if descendant.type == "function_declarator":
            return " ".join(_node_text(descendant, raw).split())
    return ""


def _qualified_declarator(symbol: GraphSymbol) -> str:
    """Qualify a parser-preserved declarator for exact overload admission."""

    if not symbol.declarator:
        return ""
    prefix, _, _ = symbol.qualified_name.rpartition("::")
    local = symbol.declarator.rsplit("::", 1)[-1]
    return f"{prefix}::{local}" if prefix else local


def _normalized_pointer_spacing(declarator: str) -> str:
    """Ignore only whitespace adjacent to pointer stars in exact anchors."""

    return re.sub(r"\s*\*\s*", "*", declarator)


def _call_names(*, node: Any, raw: bytes) -> tuple[str, ...]:
    names: set[str] = set()
    for descendant in _walk(node):
        if descendant.type != "call_expression":
            continue
        function = descendant.child_by_field_name("function") or next(iter(descendant.named_children), None)
        if function is None:
            continue
        name = re.sub(r"<[^<>]*>", "", _node_text(function, raw).strip())
        name = name.split("->")[-1].split(".")[-1].lstrip(":")
        if _CALL_NAME_RE.fullmatch(name):
            names.add(name)
    return tuple(sorted(names))


def _signature(*, node: Any, raw: bytes) -> str:
    if node.type != "function_definition":
        return ""
    body = next((child for child in node.named_children if child.type == "compound_statement"), None)
    end = body.start_byte if body is not None else node.end_byte
    return " ".join(raw[node.start_byte:end].decode("utf-8", errors="replace").split())


def _node_text(node: Any, raw: bytes) -> str:
    return raw[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Any) -> Iterable[Any]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _file_id(path: str) -> str:
    return f"file:{path}"


def _symbol_id(
    path: str,
    qualified_name: str,
    declarator: str,
    kind: str,
    line_start: int,
    digest: str,
) -> str:
    return f"symbol:{sha256_json(f'{path}|{qualified_name}|{declarator}|{kind}|{line_start}|{digest}')[:20]}"
