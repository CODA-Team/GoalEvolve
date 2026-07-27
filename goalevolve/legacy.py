from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.io import atomic_json, load_json, sha256_json


@dataclass(frozen=True)
class LegacyEvidence:
    record_id: str
    design: str
    metrics: dict[str, float]
    provenance_grade: str
    source_manifest: str
    notes: str = ""


class LegacyImporter:
    """Import only an explicit manifest; it never scans or copies historical result trees."""

    def import_manifest(self, *, manifest_path: Path, destination: Path) -> list[LegacyEvidence]:
        payload = load_json(manifest_path)
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            raise ValueError("legacy manifest must be an object with a records list")
        records: list[LegacyEvidence] = []
        for raw in payload["records"]:
            if not isinstance(raw, dict):
                raise ValueError("legacy record must be an object")
            if raw.get("result_path") or raw.get("workspace_path"):
                raise ValueError("legacy manifest stores metrics/provenance only; historical paths are forbidden")
            metrics = raw.get("metrics")
            if not isinstance(metrics, dict) or not metrics:
                raise ValueError("legacy record needs explicit metrics")
            record = LegacyEvidence(
                record_id=str(raw.get("record_id") or f"LEG_{sha256_json(raw)[:16]}"),
                design=str(raw["design"]),
                metrics={key: float(value) for key, value in metrics.items()},
                provenance_grade=str(raw.get("provenance_grade") or "unverified"),
                source_manifest=str(manifest_path.resolve()),
                notes=str(raw.get("notes") or ""),
            )
            records.append(record)
        atomic_json(
            destination,
            {
                "schema_version": "goalevolve.v2.legacy-import.v1",
                "source_manifest": str(manifest_path.resolve()),
                "records": [record.__dict__ for record in records],
            },
        )
        return records
