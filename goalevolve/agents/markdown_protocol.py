"""Strict, small Markdown protocol for direct Codex Teacher communication.

Codex is asked to reason in readable Markdown rather than emit JSON.  The
controller nevertheless needs bounded fields, so this parser recognizes only
fixed sections and ``- Field: value`` entries.  Unknown prose is retained in
the raw ``.md`` artifact but has no execution authority.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping


_H2 = re.compile(r"(?m)^##\s+(.+?)\s*$")
_H3 = re.compile(r"(?m)^###\s+(.+?)\s*$")


def _section(text: str, title: str) -> str:
    matches = list(_H2.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).strip().lower() != title.lower():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[match.end() : end].strip()
    return ""


def _blocks(section: str) -> Iterable[tuple[str, str]]:
    matches = list(_H3.finditer(section))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        yield match.group(1).strip(), section[match.end() : end].strip()


def _field(block: str, name: str) -> str:
    match = re.search(rf"(?mi)^[ \t]*-[ \t]*{re.escape(name)}[ \t]*:[ \t]*(.*?)\s*$", block)
    return match.group(1).strip() if match else ""


def _items(value: str) -> tuple[str, ...]:
    if not value or value.strip().lower() in {"none", "n/a", "-"}:
        return ()
    values = []
    for item in re.split(r"[;,]", value):
        normalized = item.strip().strip("`").strip()
        if normalized:
            values.append(normalized)
    return tuple(values)


def _recipe_id(value: str) -> str:
    """Normalize Markdown code formatting without altering a recipe identifier."""

    normalized = value.strip()
    if len(normalized) >= 2 and normalized.startswith("`") and normalized.endswith("`"):
        return normalized[1:-1].strip()
    return normalized


def _candidate_id(value: str) -> str:
    """Normalize whole-value inline-code presentation around a Candidate ID."""

    normalized = value.strip()
    if len(normalized) >= 2 and normalized.startswith("`") and normalized.endswith("`"):
        return normalized[1:-1].strip()
    return normalized


def _draft_signature_id(value: str) -> str:
    """Extract the controller-issued ``draft_N`` key from Teacher prose."""

    normalized = value.strip().strip("`").strip()
    match = re.search(r"\bdraft_\d+\b", normalized, flags=re.IGNORECASE)
    return match.group(0).lower() if match else normalized


def _source_hook_items(value: str) -> tuple[str, ...]:
    """Parse source-file fences with semicolons preferred and comma compatibility.

    Teacher plans commonly copy an AST anchor (``path::symbol``) into the
    human-readable Source Hooks line.  Execution fences are files, however;
    the symbol belongs in Source Evidence and is independently verified by
    the Controller.  Accept that readable shorthand without weakening the
    evidence requirement.
    """
    if not value or value.strip().lower() in {"none", "n/a", "-"}:
        return ()
    paths: list[str] = []
    for item in re.split(r"[;,]", value):
        normalized = item.strip().strip("`").strip()
        if not normalized:
            continue
        path, separator, _ = normalized.partition("::")
        paths.append(path.strip() if separator else normalized)
    return tuple(paths)


def _source_evidence_items(value: str) -> tuple[str, ...]:
    """Parse source anchors without splitting commas in C++ declarators.

    New Teacher output separates anchors with semicolons.  Older plans that
    used commas remain readable only when the comma begins another complete
    source path, so ``method(int count, double ratio)`` stays one anchor.
    """

    if not value or value.strip().lower() in {"none", "n/a", "-"}:
        return ()
    anchors: list[str] = []
    legacy_next_anchor = re.compile(
        r"\s*,\s*(?=[^,;\s]+\.(?:cc|cpp|cxx|h|hh|hpp)::)"
    )
    for segment in value.split(";"):
        for item in legacy_next_anchor.split(segment):
            normalized = item.strip().strip("`").strip()
            if normalized:
                anchors.append(normalized)
    return tuple(anchors)


def _bullet_items(section: str) -> tuple[str, ...]:
    """Read a compact Markdown list without granting arbitrary prose authority."""
    values = []
    for raw in section.splitlines():
        match = re.match(r"^\s*[-*]\s*(?:Idea\s*:\s*)?(.*?)\s*$", raw, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        if value:
            values.append(value)
    return tuple(values)


def _evolution_idea_records(section: str) -> tuple[dict[str, object], ...]:
    """Parse bounded idea blocks while accepting older bullet-only plans."""
    records: list[dict[str, object]] = []
    for heading, block in _blocks(section):
        idea = _field(block, "Idea") or block.splitlines()[0].strip() if block else ""
        if not idea:
            continue
        records.append(
            {
                "reference": heading,
                "idea": idea,
                "predicted_stage_effect": _field(block, "Predicted Stage Effect"),
                "source_hooks": _source_hook_items(_field(block, "Source Hooks")),
                "expected_signals": _items(_field(block, "Expected Signals")),
                "activation_signals": _items(_field(block, "Activation Signals")),
                "source_evidence": _source_evidence_items(_field(block, "Source Evidence")),
                "evaluation_recipe": _recipe_id(_field(block, "Evaluation Recipe")),
                "falsification_condition": _field(block, "Falsification Condition"),
                "internal_cpp_scheduling_suggestion": _field(
                    block, "Internal C++ Scheduling Suggestion"
                ),
                "paper_card_ids": _items(_field(block, "Paper Card References")),
                "priority": _field(block, "Priority"),
                "draft_signature_id": _draft_signature_id(
                    _field(block, "Draft Signature")
                ),
                "epd_search_query": _draft_signature_id(
                    _field(block, "EPD Search Query")
                ),
                "retrieved_historical_ideas": _items(
                    _field(block, "Retrieved Historical Ideas")
                ),
                "opened_epd_records": _items(_field(block, "Opened EPD Records")),
                "nearest_historical_idea": _field(
                    block, "Nearest Historical Idea"
                ).strip().strip("`"),
                "semantic_overlap": _field(block, "Semantic Overlap"),
                "material_difference": _field(block, "Material Difference"),
                "novelty_conclusion": _field(block, "Novelty Conclusion"),
            }
        )
    if records:
        return tuple(records)
    for index, value in enumerate(_bullet_items(section), start=1):
        match = re.match(r"^\[?(idea_\d+)\]?\s*:\s*(.+)$", value, flags=re.IGNORECASE)
        records.append(
            {
                "reference": match.group(1) if match else f"idea_{index}",
                "idea": match.group(2).strip() if match else value,
                "predicted_stage_effect": "",
                "source_hooks": (),
                "expected_signals": (),
                "source_evidence": (),
                "falsification_condition": "",
                "paper_card_ids": (),
                "priority": index - 1,
                "draft_signature_id": "",
                "epd_search_query": "",
                "retrieved_historical_ideas": (),
                "opened_epd_records": (),
                "nearest_historical_idea": "",
                "semantic_overlap": "",
                "material_difference": "",
                "novelty_conclusion": "",
            }
        )
    return tuple(records)


def _source_investigation_records(section: str) -> tuple[dict[str, object], ...]:
    """Parse the Teacher's source-read evidence without treating it as a patch plan."""
    records: list[dict[str, object]] = []
    for heading, block in _blocks(section):
        evidence = _source_evidence_items(_field(block, "Source Evidence"))
        observation = _field(block, "Observed Control Point")
        if evidence or observation:
            records.append(
                {
                    "reference": heading,
                    "source_evidence": evidence,
                    "observed_control_point": observation,
                }
            )
    return tuple(records)


def parse_teacher_plan(text: str) -> dict[str, object]:
    """Parse the executable subset of a Teacher plan Markdown response."""
    assignments = []
    for heading, block in _blocks(_section(text, "Student Assignments")):
        assignments.append(
            {
                "student_id": heading,
                "role": _field(block, "Role").lower(),
                "candidate_id": _candidate_id(_field(block, "Candidate")),
                "idea_reference": _candidate_id(_field(block, "EPD Idea")),
                "claim": _field(block, "Claim"),
                "selection_rationale": _field(block, "Selection Rationale"),
                "source_hooks": _source_hook_items(_field(block, "Source Hooks")),
                "expected_signals": _items(_field(block, "Expected Signals")),
                "activation_signals": _items(_field(block, "Activation Signals")),
                "source_evidence": _source_evidence_items(_field(block, "Source Evidence")),
                "evaluation_recipe": _recipe_id(_field(block, "Evaluation Recipe")),
                "falsification_condition": _field(block, "Falsification Condition"),
                "internal_cpp_scheduling_suggestion": _field(
                    block, "Internal C++ Scheduling Suggestion"
                ),
                "epd_record_ids": _items(_field(block, "EPD References")),
            }
        )
    idea_records = _evolution_idea_records(_section(text, "Evolution Ideas"))
    source_investigation = _source_investigation_records(_section(text, "Source Investigation"))
    parent_policy_block = _section(text, "Parent Policy")
    return {
        "diagnosis_summary": _section(text, "Diagnosis Summary"),
        "evolution_ideas": tuple(str(record["idea"]) for record in idea_records),
        "evolution_idea_records": idea_records,
        "source_investigation": source_investigation,
        "parent_policy": re.sub(
            r"(?mi)^\s*-\s*Retire Pending Ideas\s*:\s*.*$", "", parent_policy_block
        ).strip(),
        "retire_pending_ideas": _items(_field(parent_policy_block, "Retire Pending Ideas")),
        "assignments": assignments,
    }


def parse_draft_signatures(text: str) -> tuple[dict[str, object], ...]:
    """Parse Pass-A mechanism signatures without granting assignment authority."""
    signatures: list[dict[str, object]] = []
    for heading, block in _blocks(_section(text, "Draft Mechanism Signatures")):
        signatures.append(
            {
                "signature_id": heading,
                "stage": _field(block, "Stage"),
                "problem": _field(block, "Problem"),
                "source_hook": _field(block, "Source Hook"),
                "decision_type": _field(block, "Decision Type"),
                "observed_state": _field(block, "Observed State"),
                "action": _field(block, "Action"),
                "guard": _field(block, "Guard"),
                "expected_effect": _field(block, "Expected Effect"),
            }
        )
    return tuple(signatures)


def draft_signature_validation_errors(
    signatures: Iterable[Mapping[str, object]],
    *,
    minimum_count: int = 5,
) -> tuple[str, ...]:
    """Reject malformed Pass-A output before Controller retrieval begins."""
    rows = [dict(row) for row in signatures if isinstance(row, Mapping)]
    errors: list[str] = []
    if len(rows) < minimum_count:
        errors.append(f"draft_signature_count:{len(rows)}<{minimum_count}")
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        signature_id = str(row.get("signature_id") or "").strip()
        if not signature_id:
            errors.append(f"missing_draft_signature_id:{index}")
        elif signature_id in seen:
            errors.append(f"duplicate_draft_signature_id:{signature_id}")
        seen.add(signature_id)
        for field in (
            "stage",
            "problem",
            "source_hook",
            "decision_type",
            "observed_state",
            "action",
            "guard",
            "expected_effect",
        ):
            if not str(row.get(field) or "").strip():
                errors.append(f"missing_draft_signature_field:{signature_id or index}:{field}")
    return tuple(dict.fromkeys(errors))


def teacher_plan_validation_errors(
    text: str,
    *,
    required_roles: Iterable[str],
    require_explorer_ideas: bool,
    required_student_roles: Mapping[str, str] | None = None,
    require_source_investigation: bool = False,
    require_evaluation_recipe: bool = False,
) -> tuple[str, ...]:
    """Validate the executable Markdown shape before any assignment is used.

    The Teacher is free to author mechanisms, but execution authority comes
    only from this small, auditable subset.  Validation deliberately checks
    structure rather than judging the idea itself; source/history admission is
    the Controller's separate responsibility.
    """
    required_roles = tuple(str(role).strip().lower() for role in required_roles)
    required_sections = [
        "Diagnosis Summary",
        "Evolution Ideas",
        "Parent Policy",
        "Student Assignments",
    ]
    if require_source_investigation:
        required_sections.append("Source Investigation")
    errors: list[str] = []
    for title in required_sections:
        if not _section(text, title):
            errors.append(f"missing_section:{title.lower().replace(' ', '_')}")
    parsed = parse_teacher_plan(text)
    ideas = tuple(parsed.get("evolution_idea_records") or ())
    if require_explorer_ideas and len(ideas) < 5:
        errors.append(f"explorer_idea_count:{len(ideas)}<5")
    if require_explorer_ideas:
        for index, idea in enumerate(ideas, start=1):
            if not isinstance(idea, dict):
                errors.append(f"invalid_idea:{index}")
                continue
            for field in (
                "idea",
                "predicted_stage_effect",
                "source_hooks",
                "expected_signals",
                "activation_signals",
                "source_evidence",
                "falsification_condition",
            ):
                if not idea.get(field):
                    errors.append(f"missing_idea_field:{idea.get('reference') or index}:{field}")
    if require_source_investigation:
        investigations = tuple(parsed.get("source_investigation") or ())
        if len(investigations) < 2:
            errors.append(f"source_investigation_count:{len(investigations)}<2")
        for index, record in enumerate(investigations, start=1):
            if not isinstance(record, dict):
                errors.append(f"invalid_source_investigation:{index}")
                continue
            if not record.get("source_evidence"):
                errors.append(f"missing_source_investigation_field:{index}:source_evidence")
            if not record.get("observed_control_point"):
                errors.append(f"missing_source_investigation_field:{index}:observed_control_point")
    assignments = {
        str(row.get("student_id") or ""): row
        for row in list(parsed.get("assignments") or ())
        if isinstance(row, dict)
    }
    required_assignments = (
        tuple((str(student_id), str(role).lower()) for student_id, role in required_student_roles.items())
        if required_student_roles is not None
        else tuple((f"student_{index}", role) for index, role in enumerate(required_roles, start=1))
    )
    for student_id, role in required_assignments:
        assignment = assignments.get(student_id)
        if assignment is None:
            errors.append(f"missing_assignment:{student_id}")
            continue
        if str(assignment.get("role") or "").lower() != role:
            errors.append(f"assignment_role_mismatch:{student_id}:{role}")
        for field in (
            "claim",
            "selection_rationale",
            "source_hooks",
            "expected_signals",
            "activation_signals",
            "source_evidence",
            "falsification_condition",
        ):
            if not assignment.get(field):
                errors.append(f"missing_assignment_field:{student_id}:{field}")
        if require_evaluation_recipe and not str(assignment.get("evaluation_recipe") or "").strip():
            errors.append(f"missing_assignment_field:{student_id}:evaluation_recipe")
        if role == "explorer" and not str(assignment.get("idea_reference") or "").strip():
            errors.append(f"missing_assignment_field:{student_id}:epd_idea")
    ideas_by_reference = {
        str(idea.get("reference") or "").strip(): idea
        for idea in ideas
        if isinstance(idea, dict) and str(idea.get("reference") or "").strip()
    }
    for assignment in assignments.values():
        expected = set(assignment.get("expected_signals") or ())
        activation = set(assignment.get("activation_signals") or ())
        student_id = str(assignment.get("student_id") or "")
        if activation and not activation.issubset(expected):
            errors.append(f"invalid_assignment_activation_signals:{student_id}")
        if str(assignment.get("role") or "").lower() != "explorer":
            continue
        idea = ideas_by_reference.get(str(assignment.get("idea_reference") or "").strip())
        if idea is not None and activation != set(idea.get("activation_signals") or ()):
            errors.append(f"explorer_activation_signals_do_not_match_idea:{student_id}")
    for idea in ideas:
        if not isinstance(idea, dict):
            continue
        expected = set(idea.get("expected_signals") or ())
        activation = set(idea.get("activation_signals") or ())
        if activation and not activation.issubset(expected):
            errors.append(f"invalid_idea_activation_signals:{idea.get('reference') or ''}")
    return tuple(dict.fromkeys(errors))


def parse_teacher_review(text: str) -> dict[str, object]:
    """Parse the bounded next-round feedback subset of Teacher review Markdown."""
    actions = []
    for heading, block in _blocks(_section(text, "Mechanism Actions")):
        actions.append(
            {
                "mechanism_family": heading,
                "action": _field(block, "Action").lower(),
                "evidence_classification": _field(block, "Evidence Classification"),
                "rationale": _field(block, "Rationale"),
            }
        )
    causal_ledger = []
    for heading, block in _blocks(_section(text, "QoR Causal Ledger")):
        causal_ledger.append(
            {
                "student_id": _field(block, "Student") or heading,
                "leakage_delta": _field(block, "Leakage Delta"),
                "dynamic_delta": _field(block, "Dynamic Delta"),
                "tns_delta": _field(block, "TNS Delta"),
                "responsible_stage": _field(block, "Responsible Stage"),
                "cell_reversion_handoff_evidence": _field(
                    block, "Cell-Reversion/Handoff Evidence"
                ),
                "next_mechanism_requirement": _field(
                    block, "Next Mechanism Requirement"
                ),
            }
        )
    return {
        "round_assessment": _section(text, "Round Assessment"),
        "mechanism_actions": actions,
        "qor_causal_ledger": causal_ledger,
        "next_round_constraints": _section(text, "Next Round Constraints"),
    }


def teacher_review_ledger_validation_errors(
    text: str,
    *,
    required_student_ids: Iterable[str],
) -> tuple[str, ...]:
    """Require one complete causal ledger row for each evaluated Student.

    The ledger is advisory and never changes promotion, but an omitted or
    partial row would otherwise silently discard the evidence needed by the
    next Teacher/Student handoff.  Keep this validation separate from the
    plan protocol: a failed review is repaired non-blockingly and never
    invalidates an already committed QoR result.
    """

    expected = tuple(
        dict.fromkeys(
            str(student_id).strip()
            for student_id in required_student_ids
            if str(student_id).strip()
        )
    )
    if not expected:
        return ()
    parsed = parse_teacher_review(text)
    ledger = [
        dict(row)
        for row in list(parsed.get("qor_causal_ledger") or ())
        if isinstance(row, Mapping)
    ]
    by_student: dict[str, list[dict[str, object]]] = {}
    for row in ledger:
        student_id = str(row.get("student_id") or "").strip()
        if student_id:
            by_student.setdefault(student_id, []).append(row)
    errors: list[str] = []
    required_fields = (
        "leakage_delta",
        "dynamic_delta",
        "tns_delta",
        "responsible_stage",
        "cell_reversion_handoff_evidence",
        "next_mechanism_requirement",
    )
    for student_id in expected:
        rows = by_student.get(student_id, [])
        if not rows:
            errors.append(f"missing_qor_causal_ledger:{student_id}")
            continue
        if len(rows) > 1:
            errors.append(f"duplicate_qor_causal_ledger:{student_id}")
            continue
        for field in required_fields:
            if not str(rows[0].get(field) or "").strip():
                errors.append(f"missing_qor_causal_ledger_field:{student_id}:{field}")
    return tuple(dict.fromkeys(errors))


def render_teacher_plan(
    *,
    diagnosis_summary: str,
    parent_policy: str,
    assignments: Iterable[dict[str, object]],
    evolution_ideas: Iterable[str | dict[str, object]] = (),
    retire_pending_ideas: Iterable[str] = (),
) -> str:
    """Render deterministic fallback output in the same public protocol."""
    lines = ["## Diagnosis Summary", diagnosis_summary.strip(), "", "## Evolution Ideas"]
    ideas = list(evolution_ideas)
    if not ideas:
        lines.extend(["- Retain source-verified exploration within the current diagnosis."])
    for index, raw in enumerate(ideas, start=1):
        if not isinstance(raw, dict):
            lines.append(f"- {str(raw).strip()}")
            continue
        reference = str(raw.get("reference") or f"idea_{index}")
        lines.extend(
            [
                f"### {reference}",
                f"- Idea: {raw.get('idea') or ''}",
                f"- Predicted Stage Effect: {raw.get('predicted_stage_effect') or ''}",
                f"- Source Hooks: {'; '.join(str(item) for item in raw.get('source_hooks') or ()) or 'none'}",
                f"- Expected Signals: {', '.join(str(item) for item in raw.get('expected_signals') or ()) or 'none'}",
                f"- Activation Signals: {', '.join(str(item) for item in raw.get('activation_signals') or raw.get('expected_signals') or ()) or 'none'}",
                f"- Source Evidence: {'; '.join(str(item) for item in raw.get('source_evidence') or ()) or 'none'}",
                f"- Evaluation Recipe: {raw.get('evaluation_recipe') or ''}",
                f"- Falsification Condition: {raw.get('falsification_condition') or ''}",
                f"- Internal C++ Scheduling Suggestion: {raw.get('internal_cpp_scheduling_suggestion') or 'none'}",
                f"- Paper Card References: {', '.join(str(item) for item in raw.get('paper_card_ids') or ()) or 'none'}",
                f"- Priority: {raw.get('priority') if raw.get('priority') is not None else index - 1}",
                "",
            ]
        )
    retired = ", ".join(str(item) for item in retire_pending_ideas if str(item).strip()) or "none"
    lines.extend(
        [
            "",
            "## Parent Policy",
            parent_policy.strip(),
            f"- Retire Pending Ideas: {retired}",
            "",
            "## Student Assignments",
        ]
    )
    for assignment in assignments:
        lines.extend(
            [
                f"### {assignment['student_id']}",
                f"- Role: {assignment.get('role') or 'explorer'}",
                f"- Candidate: {assignment.get('candidate_id') or ''}",
                f"- EPD Idea: {assignment.get('idea_reference') or 'none'}",
                f"- Claim: {assignment.get('claim') or ''}",
                f"- Selection Rationale: {assignment.get('selection_rationale') or 'Controller-provided source-verified candidate.'}",
                f"- Source Hooks: {'; '.join(str(item) for item in assignment.get('source_hooks') or ()) or 'none'}",
                f"- Expected Signals: {', '.join(str(item) for item in assignment.get('expected_signals') or ()) or 'none'}",
                f"- Activation Signals: {', '.join(str(item) for item in assignment.get('activation_signals') or assignment.get('expected_signals') or ()) or 'none'}",
                f"- Source Evidence: {'; '.join(str(item) for item in assignment.get('source_evidence') or ()) or 'none'}",
                f"- Evaluation Recipe: {assignment.get('evaluation_recipe') or ''}",
                f"- Falsification Condition: {assignment.get('falsification_condition') or 'No verified contract improvement.'}",
                f"- Internal C++ Scheduling Suggestion: {assignment.get('internal_cpp_scheduling_suggestion') or 'none'}",
                f"- EPD References: {', '.join(str(item) for item in assignment.get('epd_record_ids') or ()) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
