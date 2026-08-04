from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .codex_runtime import CodexRuntimeConfig, PersistentCodexRunner


@dataclass(frozen=True)
class CodexNarratorConfig:
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "medium"
    retries: int = 2
    timeout_s: int = 900
    seed_home: Path = Path("outputs/codex_home")
    credential_env: Path | None = None


@dataclass(frozen=True)
class NarrativeSummaryReport:
    ok: bool
    text: str
    detail: str
    operation_id: str
    thread_id: str | None
    artifacts: dict[str, str]


class CodexNarrativeSummarizer:
    """Optional Codex observer for compact evidence narratives, never control."""

    name = "codex_narrator"

    def __init__(self, config: CodexNarratorConfig) -> None:
        self.config = config
        self.runner = PersistentCodexRunner(
            CodexRuntimeConfig(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                retries=config.retries,
                timeout_s=config.timeout_s,
                seed_home=config.seed_home,
                credential_env=config.credential_env,
            )
        )

    def summarize(
        self,
        *,
        state_root: Path,
        round_index: int,
        cwd: Path,
        purpose: str,
        evidence: Mapping[str, object],
    ) -> NarrativeSummaryReport:
        operation_id = f"r{round_index:03d}_narrator_{purpose}"
        turn = self.runner.run(
            state_root=state_root,
            identity=f"narrator_r{round_index:03d}",
            operation_id=operation_id,
            cwd=cwd,
            artifact_root=state_root / "rounds" / f"round_{round_index:03d}" / "artifacts" / "narrator" / purpose,
            prompt=self._prompt(purpose=purpose, evidence=evidence),
        )
        try:
            text = Path(str(turn.artifacts.get("codex_last_message") or "")).read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        return NarrativeSummaryReport(turn.ok, " ".join(text.split()), turn.detail, turn.operation_id, turn.thread_id, dict(turn.artifacts))

    @staticmethod
    def _prompt(*, purpose: str, evidence: Mapping[str, object]) -> str:
        return "\n".join(
            [
                "You are an observer only. Summarize the supplied controller evidence in one compact, evidence-grounded paragraph for the stated purpose.",
                "You must not edit files, run builds/flows, create a patch or recipe, select a Student, classify official evidence, or decide promotion. Do not infer facts absent from the evidence.",
                f"Purpose: {purpose}",
                "Evidence (authoritative):",
                json.dumps(dict(evidence), ensure_ascii=False, sort_keys=True),
            ]
        )
