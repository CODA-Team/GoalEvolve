"""Compact, evidence-routed context for the persistent Teacher.

The Controller remains the source of truth for all campaign records.  This
module only projects its data into a bounded reading packet: decisions stay
inline while bulky or historical evidence is addressed by an on-disk path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from ..core.models import Parent
from ..planning.cross_design_experience import cross_design_experience_packet


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[dict[str, object]]:
    return [dict(row) for row in list(value or ()) if isinstance(row, Mapping)]


def _short(value: object, *, limit: int = 320) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _number(value: object) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


class TeacherPacketBuilder:
    """Render the bounded Teacher decision packet without execution authority."""

    observation_limit = 10
    epd_record_limit = 12

    def __init__(
        self,
        *,
        contract: Mapping[str, object],
        parent: Parent,
        diagnosis: Mapping[str, object],
        epd: Mapping[str, object],
        observations: Mapping[str, object],
        schedule_memory: Mapping[str, object],
        previous_review: Mapping[str, object],
        slots: Sequence[Mapping[str, object]],
        allowed_recipe_ids: Sequence[str],
        decision_context: Mapping[str, object],
        repository_graph: Mapping[str, object] | None,
        search_policy: Mapping[str, object],
        source_root: Path | None,
        paper_cards: Sequence[Mapping[str, object]],
        historical_seeds: Sequence[Mapping[str, object]] = (),
    ) -> None:
        self.contract = dict(contract)
        self.parent = parent
        self.diagnosis = dict(diagnosis)
        self.epd = dict(epd)
        self.observations = dict(observations)
        self.schedule_memory = dict(schedule_memory)
        self.previous_review = dict(previous_review)
        self.slots = [dict(slot) for slot in slots]
        self.allowed_recipe_ids = [str(item) for item in allowed_recipe_ids]
        self.decision_context = dict(decision_context)
        self.repository_graph = dict(repository_graph or {}) if repository_graph is not None else None
        self.search_policy = dict(search_policy)
        self.source_root = source_root
        self.paper_cards = [dict(card) for card in paper_cards]
        self.historical_seeds = [dict(card) for card in historical_seeds]

    def usage_guide(self) -> list[str]:
        """Give the model a local, one-line operating guide for every slot."""
        return [
            "## Packet Usage Guide",
            "Read the short decision facts first and open a path only when a claim needs evidence; never infer a result that is absent from the packet or its referenced artifact.",
            "- **Goal Contract** — Treat this as immutable success criteria. Compare proposed mechanisms against its parent QoR and targets. Do not use runtime as a QoR signal. Leave promotion to the Controller.",
            "- **Active Decision Stage** — Start from the stage, bottleneck, guards, and falsification rule. Use the reduced JSON only to resolve a field precisely. Do not broaden a Controller-owned recipe. Keep every idea inside the displayed boundary.",
            "- **Diagnosis** — Use checkpoint effects to locate where a gain appears or disappears. Distinguish stage-local improvement from final post-route retention. Delegate unresolved causal questions to a bounded Student investigation. Do not repeat the parent facts already shown above.",
            "- **EPD (idea lifecycle and compact attempts)** — First form a draft mechanism signature, then query the catalog and open the nearest idea, attempt, reflection, and diff paths. Use the inline states and summaries to select what merits reading. Record novelty evidence in every Explorer idea. Do not copy historical patches blindly.",
            "- **Observation Memory** — Treat these ten-or-fewer lessons as compressed empirical feedback. Avoid a disproven family or explain a materially different boundary. Prefer mechanisms with measured activation and retained benefit. Open full records only if this compact view is insufficient.",
            "- **Cross-Design Iteration Experience** — Treat the checked-in, stage-relevant lessons as process constraints distilled from completed official-flow campaigns. They never supply a patch or override the local EPD, source boundary, frozen QoR contract, or Controller promotion rule.",
            "- **Timing Schedule / Cell-Reversal Memory** — Use this only to understand measured schedule interactions and to select from Controller recipes. Never edit Tcl or create a new recipe. Keep a source mechanism separate from a schedule recommendation. Follow the stated reversal and retention evidence.",
            "- **Student Reflection Digest** — Read this as implementation-level feedback from completed attempts. Reuse an actionable lesson only after checking its reflection and diff path in EPD. Turn a limitation into a bounded enhancer hypothesis rather than an ungrounded rewrite. The Controller still owns verdicts.",
            "- **Controller Role Envelopes** — Fill every supplied role and keep its source/evaluation boundary. Explorer creates a novel mechanism, Enhancer reinforces supported evidence, and Integrator checks compatibility. Choose only a listed recipe. Do not invent Students, parents, or EPD records.",
            (
                "- **Live Source Access / P0-rooted Source Graph** — Use the focused graph to localize code, then run at least two successful read-only rg/sed inspections of the live parent. Quote real path::symbol anchors. A live graph-resolvable off-slice hook is allowed only with explicit causality, a distinct boundary, and falsification condition. The graph never authorizes a patch by itself."
                if self.repository_graph is not None
                else "- **Live Source Access / OpenROAD Cards** — Start from the supplied OpenROAD mechanism cards and run at least two successful read-only rg/sed inspections of the live parent before naming a hook. Quote real path::symbol anchors and a distinct falsification condition. No AST graph is available or implied in this mode."
            ),
            "- **Evidence-only Search Policy / Paper Cards** — Use the compact stagnation facts to diversify and paper cards for concepts, not patch recipes. The listed parent is the only parent. The Controller alone validates evidence and promotion. Full policy and literature details remain path-routed.",
            "",
        ]

    def sections(self) -> list[str]:
        sections: list[str] = []
        sections.extend(self._goal_contract())
        sections.extend(self._active_stage())
        sections.extend(self._diagnosis())
        sections.extend(self._epd())
        sections.extend(self._observations())
        sections.extend(self._cross_design_experience())
        sections.extend(self._schedule_memory())
        sections.extend(self._student_reflection_digest())
        sections.extend(self._role_envelopes())
        sections.extend(self._live_source())
        sections.extend(self._source_graph())
        sections.extend(self._historical_seeds())
        sections.extend(self._search_policy())
        sections.extend(self._paper_cards())
        return sections

    def draft_sections(self) -> list[str]:
        """Return the source-local decision facts needed before EPD retrieval."""
        sections: list[str] = []
        sections.extend(self._goal_contract())
        sections.extend(self._active_stage())
        sections.extend(self._diagnosis())
        sections.extend(self._cross_design_experience())
        sections.extend(self._live_source())
        sections.extend(self._source_graph())
        return sections

    def _goal_contract(self) -> list[str]:
        metrics = _rows(self.contract.get("metrics"))
        if not metrics:
            baseline = _mapping(self.contract.get("baseline_metrics"))
            metrics = [
                {"name": name, "baseline": value, "target": "<unspecified>"}
                for name, value in baseline.items()
            ]
        lines = [
            "## Goal Contract",
            "This is the immutable QoR contract and the only decision basis for success. Read it before forming an idea; the parent QoR below replaces the former separate Parent slot.",
            f"Design: {self.contract.get('design') or '<unspecified>'}",
            "Optimize every listed QoR metric under the frozen contract. The Controller computes goal distance and promotion; runtime is telemetry only.",
        ]
        for metric in metrics:
            name = str(metric.get("name") or "metric")
            direction = "≤" if bool(metric.get("minimize", True)) else "≥"
            lines.append(
                f"- {name}: baseline {_number(metric.get('baseline'))} → target {direction} {_number(metric.get('target'))}"
            )
        parent_qor = ", ".join(
            f"{name}={_number(value)}" for name, value in self.parent.metrics.items()
            if str(name).lower() not in {"runtime_s", "tool_runtime", "tool_runtime_s", "flow_runtime", "flow_runtime_s"}
        )
        lines.extend(
            [
                f"Parent QoR: {parent_qor or '<no measured QoR>'}",
                f"Current parent: {self.parent.parent_id}; parent goal distance: {_number(self.parent.goal_distance)}.",
                "",
            ]
        )
        return lines

    def _active_stage(self) -> list[str]:
        context = self.decision_context
        stage = str(context.get("stage") or context.get("mode") or "single_stage")
        primary = context.get("dominant_metric") or context.get("primary_metrics") or self.diagnosis.get("dominant_bottleneck") or "<unspecified>"
        guards = context.get("guards") or context.get("protected_metrics") or "preserve Controller guards, zero DRV, and official evidence"
        falsification = context.get("teacher_falsification_rule") or context.get("falsification_rule") or "use the Controller-provided official evidence rule"
        reduced = {
            key: context[key]
            for key in (
                "stage",
                "mode",
                "dominant_metric",
                "primary_metrics",
                "normalized_residuals",
                "evaluation_mode",
                "checkpoint_effect_semantics",
                "full_contract_distance_required_for_promotion",
                "teacher_falsification_rule",
                "falsification_rule",
            )
            if key in context
        }
        return [
            "## Active Decision Stage",
            "This tells you what must be improved this round and what would falsify the mechanism. Use its summary for planning and its reduced JSON only for exact Controller semantics.",
            f"Current stage: {stage}",
            f"Primary bottleneck: {primary}",
            f"Evaluation mode: {context.get('evaluation_mode') or '<controller default>'}",
            f"Guards: {_short(guards)}",
            f"Falsification: {_short(falsification)}",
            _json(reduced or {"mode": "single_stage"}),
            "",
        ]

    def _diagnosis(self) -> list[str]:
        reduced = {
            key: self.diagnosis[key]
            for key in (
                "dominant_bottleneck",
                "dominant_residual",
                "residuals",
                "responsible_stage",
                "checkpoint_effects",
                "checkpoint_effect_semantics",
                "unresolved_debt",
                "baseline_policy",
            )
            if key in self.diagnosis
        }
        return [
            "## Diagnosis",
            "This isolates the stage-level cause of the active QoR gap without duplicating Goal Contract or Parent data. In particular, checkpoint effects reveal whether a local gain is lost during a later phase and can justify a bounded Enhancer investigation.",
            _json(reduced),
            "",
        ]

    def _epd_paths(self) -> dict[str, str]:
        authority = str(self.epd.get("full_epd_artifact") or "")
        root = Path(authority).parent / "epd" if authority else Path("knowledge/epd")
        return {
            "epd_root": str(root),
            "idea_catalog": str(root / "indexes" / "idea_catalog.jsonl"),
            "retrieval_corpus": str(root / "indexes" / "retrieval_corpus.jsonl"),
            "full_manifest": str(root / "manifest.json"),
            "explorer_view": str(root / "round_views" / "<round>" / "explorer_view.json"),
            "enhancer_view": str(root / "round_views" / "<round>" / "enhancer_view.json"),
            "integrator_view": str(root / "round_views" / "<round>" / "integrator_view.json"),
        }

    def _epd(self) -> list[str]:
        paths = self._epd_paths()
        project_root = Path(__file__).resolve().parents[2]
        search_tool = f"PYTHONPATH={project_root} python -m goalevolve.epd_search"
        records = _rows(self.epd.get("decision_records"))[: self.epd_record_limit]
        compact_records = [
            {
                key: record[key]
                for key in (
                    "record_id",
                    "idea_id",
                    "round_index",
                    "mechanism_family",
                    "epd_status",
                    "evidence_state",
                    "metrics",
                    "phase_signals",
                    "goal_distance",
                    "distance_gain",
                    "implementation_diff_artifact",
                )
                if key in record
            }
            for record in records
        ]
        pending = [
            {
                "idea_id": idea.get("idea_id"),
                "status": idea.get("status"),
                "idea": _short(idea.get("idea"), limit=480),
                "idea_path": str(Path(paths["epd_root"]) / "ideas" / str(idea.get("idea_id") or "<IDEA_ID>") / "idea.json"),
            }
            for idea in _rows(self.epd.get("pending_ideas"))[: self.epd_record_limit]
        ]
        return [
            "## EPD (idea lifecycle and compact attempts)",
            "Use the EPD as a path-addressable idea, attempt, reflection, and mechanism database; do not paste or assume its full history. Explorer forms a draft signature before searching, Enhancer opens the selected dossier, and Integrator checks mechanism-card read/write boundaries.",
            "Workers may use a dedicated round directory while source inspection resolves through the supplied live-source root. Use supplied absolute EPD and graph paths verbatim; do not make them relative to the working directory. Invoke the displayed search_tool exactly so the GoalEvolve package is importable.",
            "Explorer retrieval entry:",
            _json({**paths, "search_tool": search_tool, "search_scope": ["pending", "unactivated", "invalid", "promising", "validated"]}),
            "Object convention: `ideas/<IDEA_ID>/idea.json`; `attempts/<ATTEMPT_ID>/attempt.json`; `attempts/<ATTEMPT_ID>/student_reflection.md`; `attempts/<ATTEMPT_ID>/implementation.diff`; `mechanisms/<MECHANISM_ID>/mechanism_card.json`.",
            "Inline lifecycle facts (read the referenced object before relying on a detail):",
            _json({"status_counts": self.epd.get("status_counts") or {}, "pending_ideas": pending, "recent_decision_records": compact_records}),
            "",
        ]

    def _observations(self) -> list[str]:
        lessons = []
        for row in _rows(self.observations.get("family_hook_summary"))[: self.observation_limit]:
            lessons.append(
                {
                    "family": row.get("mechanism_family"),
                    "source_hooks": list(row.get("source_hooks") or ()),
                    "activated": int(row.get("activation_count") or 0) > 0,
                    "result": row.get("last_state"),
                    "best_distance_gain": row.get("best_distance_gain"),
                    "failure": _short(row.get("last_failure_signature"), limit=240),
                }
            )
        return [
            "## Observation Memory",
            "This is a bounded empirical lesson list for the active search, not the full observation ledger. Use it to avoid repeated failures, recognize activated-but-unpromoted mechanisms, and compare the best and worst observed effects near the current source focus.",
            _json({"active_stage_lessons": lessons, "full_observation_memory": self.observations.get("full_observation_memory") or "knowledge/observations.json"}),
            "",
        ]

    def _schedule_memory(self) -> list[str]:
        epd_authority = str(self.epd.get("full_epd_artifact") or "")
        default_path = str(Path(epd_authority).parent / "timing_schedule_memory.json") if epd_authority else "knowledge/timing_schedule_memory.json"
        rollup = _mapping(self.schedule_memory.get("recipe_rollup"))
        compact_rollup = {key: rollup[key] for key in list(sorted(rollup))[:8]}
        return [
            "## Timing Schedule / Cell-Reversal Memory",
            "This summarizes only measured controller-owned recipe interactions and cell-reversal evidence. It informs source-level hypotheses but never authorizes a Tcl edit, recipe invention, or a promotion decision.",
            _json({"recommended_recipe_ids": list(self.schedule_memory.get("recommended_recipe_ids") or ()), "recipe_rollup": compact_rollup, "recent": _rows(self.schedule_memory.get("recent"))[-4:], "full_schedule_memory": self.schedule_memory.get("full_schedule_memory_artifact") or default_path}),
            "",
        ]

    def _student_reflection_digest(self) -> list[str]:
        reflections = _rows(self.previous_review.get("student_reflections"))
        if not reflections:
            reflections = _rows(self.previous_review.get("outcomes"))
        compact = [
            {
                "attempt_id": row.get("record_id") or row.get("attempt_id") or row.get("hypothesis_id"),
                "classification": row.get("epd_status") or row.get("evidence_state") or _mapping(row.get("verdict")).get("state"),
                "lesson": _short(row.get("reflection") or row.get("rationale") or row.get("evaluation_error"), limit=400),
                "reflection_path": row.get("student_reflection_path") or row.get("reflection_path"),
            }
            for row in reflections[-8:]
        ]
        if not compact and self.previous_review:
            compact = [{"attempt_id": "previous_round", "classification": "review_summary", "lesson": _short(self.previous_review.get("round_assessment")), "reflection_path": "consult EPD attempt student_reflection.md"}]
        return [
            "## Student Reflection Digest",
            "This replaces the large prior Teacher-review payload with completed Student implementation lessons. Treat it as advisory evidence, then open the recorded reflection and diff before proposing an enhancement or integration.",
            _json({"reflections": compact, "full_round_review": self.previous_review.get("artifact_path") or "rounds/<previous>/teacher_review.json"}),
            "",
        ]

    def _cross_design_experience(self) -> list[str]:
        packet = cross_design_experience_packet(
            decision_context=self.decision_context,
        )
        return [
            "## Cross-Design Iteration Experience",
            "These are compact, checked-in lessons from completed official-flow campaigns. Apply a relevant lesson as a planning and implementation constraint, then validate it against the live parent and local EPD. It is never a patch recipe or promotion authority.",
            _json(packet),
            "",
        ]

    def _role_envelopes(self) -> list[str]:
        return [
            "## Controller Role Envelopes",
            "These are the only allocatable Student slots and source/evaluation boundaries. Preserve every supplied role; use Explorer for novel source mechanisms, Enhancer for bounded reinforcement, and Integrator only after checking compatibility evidence.",
            _json(self.slots),
            "The Controller owns Tcl and exposes only these evaluation recipes. Select exactly one recipe per idea and assignment; the same recipe is used for that Student and its no-diff parent baseline. Do not invent a recipe or modify Tcl.",
            _json(self.allowed_recipe_ids),
            "",
        ]

    def _live_source(self) -> list[str]:
        return [
            "## Live Source Access",
            "This is the read-only current parent snapshot. Use at least two successful rg/sed inspections before naming a hook; the Controller validates each quoted `path::symbol` anchor against this source.",
            f"Read-only parent source root: {self.source_root or '<not supplied>'}",
            "",
        ]

    def _source_graph(self) -> list[str]:
        if self.repository_graph is None:
            return [
            "## Source Localization",
            "OpenROAD-card mode is active: use the supplied mechanism cards and live rg/sed inspection only; the Controller validates every path::symbol anchor against the current parent source. No AST graph is built or injected into this run.",
                "",
            ]
        graph = self.repository_graph
        root = str(graph.get("artifact_root") or "")
        focus = _mapping(graph.get("focus"))
        graph_data = focus or graph
        cards = _rows(graph_data.get("cards"))
        symbol_cards = sorted(
            (card for card in cards if str(card.get("node_kind") or "") != "file"),
            key=lambda card: (
                str(card.get("qualified_name") or ""),
                str(card.get("path") or ""),
            ),
        )
        file_cards = sorted(
            (card for card in cards if str(card.get("node_kind") or "") == "file"),
            key=lambda card: str(card.get("path") or ""),
        )
        # A file card is useful context, but cannot explain a control path.
        # Reserve most of the compact budget for symbols before adding a few
        # files as location aids.
        selected_cards = [
            *symbol_cards[:12],
            *file_cards[: min(4, max(0, 16 - min(12, len(symbol_cards))))],
        ]
        card_summary = [
            {
                key: card[key]
                for key in ("path", "qualified_name", "declarator", "metric_signals")
                if key in card
            }
            for card in selected_cards
        ]
        selected_by_id = {
            str(card.get("card_id") or ""): card
            for card in selected_cards
            if str(card.get("card_id") or "")
        }
        relationships: list[str] = []
        for edge in _rows(graph_data.get("edges")):
            if str(edge.get("kind") or "") not in {"calls", "includes"}:
                continue
            source = selected_by_id.get(str(edge.get("source") or ""))
            target = selected_by_id.get(str(edge.get("target") or ""))
            if source is None or target is None:
                continue
            source_name = str(source.get("qualified_name") or source.get("path") or "")
            target_name = str(target.get("qualified_name") or target.get("path") or "")
            if source_name and target_name:
                arrow = "→" if edge.get("kind") == "calls" else "includes"
                relationships.append(f"{source_name} {arrow} {target_name}")
        relationships = list(dict.fromkeys(relationships))[:8]
        paths = {
            "focused_graph_path": graph.get("focused_graph_path") or (str(Path(root) / "focus.json") if root else "rounds/<round>/repository_graph_focus.json"),
            "full_index_path": graph.get("full_index_path") or (str(Path(root) / "doc_cards.json") if root else "knowledge/repository_graph/<parent>/doc_cards.json"),
            "full_graph_path": graph.get("full_graph_path") or (str(Path(root) / "graph.json") if root else "knowledge/repository_graph/<parent>/graph.json"),
        }
        focused_files = graph.get("focused_files") or sorted({str(card.get("path") or "") for card in cards if card.get("path")})[:12]
        return [
            "## P0-rooted Source Graph and Doc Cards",
            "This compact graph localizes likely execution paths; it is rooted in the frozen P0 snapshot and refreshed for the current parent. Read the focused graph, then verify live source with rg/sed before using a symbol; a Doc Card is evidence of location, not a patch prescription or a restriction to the focus slice.",
            _json({"source_hash": graph_data.get("source_hash") or graph.get("source_hash"), "base_source_hash": graph_data.get("base_source_hash") or graph.get("base_source_hash"), "entry_chain": graph.get("entry_chain") or [], "focused_files": focused_files, "paths": paths, "cards": card_summary, "relationships": relationships}),
            "Use a Doc Card's full `declarator` verbatim when a function name is overloaded. Separate multiple Source Evidence anchors with semicolons, never commas, because a C++ declarator may contain commas.",
            "",
        ]

    def _historical_seeds(self) -> list[str]:
        if not self.historical_seeds:
            return []
        compact = [
            {
                key: seed[key]
                for key in (
                    "seed_id",
                    "source_anchors",
                    "decision_boundary",
                    "summary",
                    "expected_signals",
                    "activation_signals",
                    "reference_diff_paths",
                )
                if key in seed
            }
            for seed in self.historical_seeds
        ]
        return [
            "## Historical Mechanism Seeds (revalidation only)",
            "These cards describe source boundaries observed in an earlier campaign. They cannot supply a parent, QoR metric, or promotion. A Student must create a new diff and the Controller must re-run the complete official flow from this fresh lineage before any decision.",
            "Every listed anchor has resolved in the current parent graph; use it only after live-source inspection and a distinct falsification condition.",
            _json(compact),
            "",
        ]

    def _search_policy(self) -> list[str]:
        diversification = _mapping(self.search_policy.get("diversification"))
        hill_climb = _mapping(self.search_policy.get("hill_climb"))
        policy_path = self.search_policy.get("artifact_path") or "rounds/<round>/search_policy.json"
        compact = {
            "no_promotion_streak": int(self.search_policy.get("no_promotion_streak") or 0),
            "forbidden_exact_reuse": list(diversification.get("avoid_exact_source_hooks") or ()),
            "required_novelty": "different source hook or materially different decision boundary",
            "allowed_parent": hill_climb.get("incumbent_parent_id") or self.parent.parent_id,
            "promotion_authority": "Controller only",
            "full_search_policy": policy_path,
        }
        return [
            "## Evidence-only Search Policy",
            "This is compact Controller-derived navigation from committed evidence. It tells you when the search is stagnating and which exact reuse is forbidden; it cannot choose a parent, create a role, or promote a candidate.",
            f"no_promotion_streak: {compact['no_promotion_streak']}",
            f"promotion_authority: {compact['promotion_authority']}",
            _json(compact),
            "This policy is advisory. The listed incumbent is the only current parent; only the deterministic Controller can promote a candidate or alter roles. When exact reuse is forbidden, use a different source-grounded hook or explain the materially distinct boundary and falsification condition.",
            "",
        ]

    def _paper_cards(self) -> list[str]:
        compact = [
            {
                key: card[key]
                for key in ("card_id", "title", "topic", "summary")
                if key in card
            }
            for card in self.paper_cards[:12]
        ]
        return [
            "## Paper Card References",
            "These are concept references only. You may cite a supplied ID to explain an algorithmic idea, but no card authorizes a historical patch, an unverified result, or a change to the Controller boundary.",
            _json(compact),
            "",
        ]
