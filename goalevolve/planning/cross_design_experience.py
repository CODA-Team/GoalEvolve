"""Curated, cross-design lessons for source-evolution prompts.

Campaign-local EPD and observation memory remain the evidence authority for a
specific parent.  This library is deliberately separate: it records compact,
versioned process lessons that have been reviewed across completed campaigns.
It never supplies a patch, changes a QoR contract, or participates in
promotion.  Teacher and Student prompts receive only stage-relevant entries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..core.io import load_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATH = (
    PROJECT_ROOT
    / "docs"
    / "superpowers"
    / "knowledge"
    / "cross_design_iteration_experience.json"
)


def load_cross_design_experience(
    path: Path = LIBRARY_PATH,
) -> dict[str, object]:
    """Load the checked-in library without making a missing file fatal.

    A prompt remains usable in a source export that intentionally omits
    documentation.  Malformed rows are ignored rather than being elevated to
    Controller authority.
    """
    payload = load_json(path, {}) or {}
    if not isinstance(payload, Mapping):
        payload = {}
    lessons = [
        dict(row)
        for row in list(payload.get("lessons") or ())
        if isinstance(row, Mapping)
        and str(row.get("lesson_id") or "").strip()
        and str(row.get("planning_rule") or "").strip()
        and str(row.get("student_rule") or "").strip()
    ]
    return {
        "schema_version": str(
            payload.get("schema_version")
            or "goalevolve.cross-design-experience.v1"
        ),
        "library_path": str(path),
        "lesson_count": len(lessons),
        "lessons": lessons,
    }


def cross_design_experience_packet(
    *,
    decision_context: Mapping[str, object] | None = None,
    limit: int = 6,
) -> dict[str, object]:
    """Return compact lessons relevant to the Controller's active stage."""
    context = dict(decision_context or {})
    stage = str(context.get("stage") or "").strip()
    library = load_cross_design_experience()
    selected: list[dict[str, object]] = []
    for raw in list(library["lessons"]):
        lesson = dict(raw)
        scope = dict(lesson.get("scope") or {})
        stages = {
            str(item).strip()
            for item in list(scope.get("stages") or ())
            if str(item).strip()
        }
        if stages and stage not in stages:
            continue
        selected.append(
            {
                "lesson_id": str(lesson["lesson_id"]),
                "title": str(lesson.get("title") or ""),
                "scope": scope,
                "evidence_summary": str(lesson.get("evidence_summary") or ""),
                "planning_rule": str(lesson["planning_rule"]),
                "student_rule": str(lesson["student_rule"]),
                "review_rule": str(lesson.get("review_rule") or ""),
            }
        )
        if len(selected) >= max(1, limit):
            break
    return {
        "schema_version": library["schema_version"],
        "library_path": library["library_path"],
        "active_stage": stage or None,
        "available_lesson_count": library["lesson_count"],
        "lessons": selected,
    }
