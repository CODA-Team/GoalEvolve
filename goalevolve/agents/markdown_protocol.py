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
    for item in value.split(","):
        normalized = item.strip().strip("`").strip()
        if normalized:
            values.append(normalized)
    return tuple(values)


def _source_hook_items(value: str) -> tuple[str, ...]:
    """Parse source paths with semicolons preferred and comma compatibility."""
    if not value or value.strip().lower() in {"none", "n/a", "-"}:
        return ()
    return tuple(
        normalized
        for item in re.split(r"[;,]", value)
        if (normalized := item.strip().strip("`").strip())
    )


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
                "source_evidence": _source_evidence_items(_field(block, "Source Evidence")),
                "evaluation_recipe": _field(block, "Evaluation Recipe"),
                "falsification_condition": _field(block, "Falsification Condition"),
                "internal_cpp_scheduling_suggestion": _field(
                    block, "Internal C++ Scheduling Suggestion"
                ),
                "paper_card_ids": _items(_field(block, "Paper Card References")),
                "priority": _field(block, "Priority"),
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
                "candidate_id": _field(block, "Candidate"),
                "idea_reference": _field(block, "EPD Idea"),
                "claim": _field(block, "Claim"),
                "selection_rationale": _field(block, "Selection Rationale"),
                "source_hooks": _source_hook_items(_field(block, "Source Hooks")),
                "expected_signals": _items(_field(block, "Expected Signals")),
                "source_evidence": _source_evidence_items(_field(block, "Source Evidence")),
                "evaluation_recipe": _field(block, "Evaluation Recipe"),
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
    require_source_investigation = require_source_investigation or "explorer" in required_roles
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
            "source_evidence",
            "falsification_condition",
        ):
            if not assignment.get(field):
                errors.append(f"missing_assignment_field:{student_id}:{field}")
        if require_evaluation_recipe and not str(assignment.get("evaluation_recipe") or "").strip():
            errors.append(f"missing_assignment_field:{student_id}:evaluation_recipe")
        if role == "explorer" and not str(assignment.get("idea_reference") or "").strip():
            errors.append(f"missing_assignment_field:{student_id}:epd_idea")
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
    return {
        "round_assessment": _section(text, "Round Assessment"),
        "mechanism_actions": actions,
        "next_round_constraints": _section(text, "Next Round Constraints"),
    }


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
                f"- Source Evidence: {'; '.join(str(item) for item in assignment.get('source_evidence') or ()) or 'none'}",
                f"- Evaluation Recipe: {assignment.get('evaluation_recipe') or ''}",
                f"- Falsification Condition: {assignment.get('falsification_condition') or 'No verified contract improvement.'}",
                f"- Internal C++ Scheduling Suggestion: {assignment.get('internal_cpp_scheduling_suggestion') or 'none'}",
                f"- EPD References: {', '.join(str(item) for item in assignment.get('epd_record_ids') or ()) or 'none'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
