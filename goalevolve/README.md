# Implementation map

| Package | Owns | Must not own |
|---|---|---|
| `core/` | contracts, typed records, deterministic I/O, provenance, plugin protocols | policy decisions or tool execution |
| `planning/` | diagnosis, retrieval cards, scope, EPD, observations, timing-recipe memory | source edits or promotion writes |
| `agents/` | Teacher/Student packets and project-local Codex workers | build/flow semantics |
| `execution/` | isolated workspaces, command policy, preflight, round orchestration | contest score definitions |
| `evaluation/` | OpenROAD flow, official checks, evidence, promotion, observers | LLM prompt construction |
| `testing/` | deterministic mock evaluator used by smoke/unit tests | artifact claims |

The round owner is `execution/engine.py`. It creates a diagnosis, gets diverse source-scoped fallback hypotheses, lets Teacher refine them, asks isolated Students to edit, returns build/flow/telemetry failures to the same Student, records EPD/observations, and promotes only verified evidence. `evaluation/sfinal.py`, `evaluation/leaderboard.py`, and `token_ledger.py` are observer-only.

Run `PYTHONPATH=. python3 -m goalevolve.cli --help` for public commands.
