from __future__ import annotations

import re
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .retrieval import MechanismCard


_METRIC = re.compile(r"METRIC\|([A-Za-z0-9_]+)")
_FUNCTION = re.compile(r"(?:^|\n)\s*[\w:<>~,\s*&*]+\s+([A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:const\s*)?\{")


@dataclass(frozen=True)
class ScopeDecision:
    files: tuple[str, ...]
    observed_metrics: tuple[str, ...]
    observed_symbols: tuple[str, ...]
    confidence: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SourceScopeResolver:
    """A lightweight white-box source index for defensible edit-scope decisions.

    It is deliberately not a fixed file list: a card's proposed hook is accepted
    only when it exists in the pinned source tree, and expected telemetry/symbols
    are recorded for the Teacher/Student packet. A clang-AST resolver can replace
    this class through the same `resolve` interface in a future ablation.
    """

    def __init__(self, source_root: Path | None = None, *, include_roots: tuple[str, ...] = ()) -> None:
        self.source_root = source_root
        self.include_roots = tuple(root.strip("/") for root in include_roots if root.strip("/"))
        self._index: dict[str, tuple[set[str], set[str]]] | None = None

    def _build_index(self) -> dict[str, tuple[set[str], set[str]]]:
        if not self.source_root or not self.source_root.is_dir():
            return {}
        index: dict[str, tuple[set[str], set[str]]] = {}
        roots = [self.source_root / root for root in self.include_roots] or [self.source_root / "src"]
        roots = [root for root in roots if root.is_dir()] or [self.source_root]
        ignored_parts = {"test", "tests", "third-party", "build", "build_power", "__pycache__"}
        for implementation_root in roots:
            for directory, children, filenames in os.walk(implementation_root):
                children[:] = [child for child in children if child not in ignored_parts]
                for filename in filenames:
                    path = Path(directory) / filename
                    if path.suffix not in {".cc", ".cpp", ".cxx", ".hh", ".hpp", ".h"}:
                        continue
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    rel = str(path.relative_to(self.source_root))
                    index[rel] = (set(_METRIC.findall(text)), set(_FUNCTION.findall(text)))
        return index

    @property
    def index(self) -> dict[str, tuple[set[str], set[str]]]:
        if self._index is None:
            self._index = self._build_index()
        return self._index

    def resolve(self, card: MechanismCard) -> ScopeDecision:
        if not self.index:
            return ScopeDecision(card.source_hooks, (), (), "declared_only")
        hook_names = {Path(hook).name for hook in card.source_hooks}
        selected = [path for path in self.index if path in card.source_hooks or Path(path).name in hook_names]
        if not selected:
            # Do not hallucinate a source path. Search signal-bearing files only
            # when the explicitly named hook moved in a newer OpenROAD revision.
            signal_terms = {
                signal.lower()
                for signal in (*card.expected_signals, *card.activation_signals)
            }
            for path, (metrics, symbols) in self.index.items():
                if signal_terms.intersection(item.lower() for item in metrics | symbols):
                    selected.append(path)
        selected = sorted(selected)[:4]
        metrics = sorted({metric for path in selected for metric in self.index[path][0]})
        symbols = sorted({symbol for path in selected for symbol in self.index[path][1]})[:16]
        confidence = "verified_hook" if any(path in card.source_hooks for path in selected) else ("telemetry_inferred" if selected else "unresolved")
        return ScopeDecision(tuple(selected), tuple(metrics), tuple(symbols), confidence)
