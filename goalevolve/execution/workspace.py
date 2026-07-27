from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..core.io import atomic_json, sha256_json
from ..core.models import Parent


def clone_source_tree(source: Path, destination: Path) -> None:
    """Copy an OpenROAD source tree without build products or VCS state.

    The campaign uses this both for Student isolation and to preserve the
    exact source that produced an official evaluation before a same-Student
    telemetry-only repair is attempted.  Reflinks keep that evidence archive
    cheap on the campaign filesystem while the fallback remains portable.
    """
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    entries = [path for path in source.iterdir() if not path.name.startswith("build") and path.name not in {".git", "__pycache__"}]
    for reflink in (True, False):
        if not reflink:
            shutil.rmtree(destination)
            destination.mkdir(parents=True)
        ok = True
        for entry in entries:
            command = ["cp", "-a"] + (["--reflink=always"] if reflink else []) + [str(entry), str(destination)]
            if subprocess.run(command, text=True, capture_output=True, check=False).returncode != 0:
                ok = False
                break
        if ok:
            return
    raise RuntimeError(f"unable to clone source tree: {source}")


class IsolatedWorkspace:
    """Per-student copy-on-write workspaces; no candidate shares mutable source state."""

    name = "isolated_workspace"

    def __init__(self, source_root: Path | None = None) -> None:
        self.source_root = source_root

    def _parent_source(self, *, state_root: Path, parent: Parent) -> Path:
        return state_root / "parents" / parent.source_hash / "source"

    @staticmethod
    def _complete_marker(source: Path) -> Path:
        return source.parent / ".source_snapshot_complete"

    def ensure_parent(self, *, state_root: Path, parent: Parent) -> None:
        """Materialize one immutable source snapshot for a common parent."""
        snapshot = self._parent_source(state_root=state_root, parent=parent)
        marker = self._complete_marker(snapshot)
        if snapshot.exists() and marker.is_file():
            return
        if snapshot.exists():
            shutil.rmtree(snapshot)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if self.source_root and self.source_root.is_dir():
            clone_source_tree(self.source_root, snapshot)
        else:
            snapshot.mkdir()
        marker.write_text("complete\n", encoding="utf-8")

    def promote_candidate(self, *, state_root: Path, parent: Parent, candidate: Parent, candidate_source: Path | None, candidate_artifacts: dict[str, str] | None = None) -> None:
        """Persist the selected student's source tree as the next round's parent."""
        if candidate_source is None or not candidate_source.is_dir():
            return
        destination = self._parent_source(state_root=state_root, parent=candidate)
        marker = self._complete_marker(destination)
        if destination.exists() and marker.is_file():
            return
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        clone_source_tree(candidate_source, destination)
        marker.write_text("complete\n", encoding="utf-8")
        for artifact_key, destination_name in (("checkpoint_metrics", "checkpoints.json"), ("checkpoint_post_route_db", "design_checkpoint.odb")):
            source_artifact = Path(str((candidate_artifacts or {}).get(artifact_key) or ""))
            if source_artifact.is_file():
                shutil.copy2(source_artifact, destination.parent / destination_name)

    def prepare(self, *, state_root: Path, round_index: int, student_id: str, parent: Parent) -> Path:
        root = state_root / "rounds" / f"round_{round_index:03d}" / "students" / student_id
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        source = workspace / "source"
        marker = self._complete_marker(source)
        if not source.exists() or not marker.is_file():
            self.ensure_parent(state_root=state_root, parent=parent)
            parent_source = self._parent_source(state_root=state_root, parent=parent)
            clone_source_tree(parent_source, source)
            marker.write_text("complete\n", encoding="utf-8")
        atomic_json(
            root / "workspace_manifest.json",
            {
                "schema_version": "goalevolve.v2.workspace.v1",
                "parent_id": parent.parent_id,
                "parent_source_commit": parent.source_commit,
                "parent_source_hash": parent.source_hash,
                # The evaluator must diff a Student against this exact shared
                # parent, not against the campaign's pristine seed.  A
                # lineage can legitimately contain earlier accepted changes;
                # treating those as this Student's edit both muddies causal
                # attribution and defeats the stage-specific patch boundary.
                "parent_source": str(self._parent_source(state_root=state_root, parent=parent)),
                "workspace_hash": sha256_json({"round": round_index, "student": student_id, "parent": parent.parent_id}),
                "parent_design_checkpoint": str((self._parent_source(state_root=state_root, parent=parent).parent / "design_checkpoint.odb")),
                "parent_checkpoint_metrics": str((self._parent_source(state_root=state_root, parent=parent).parent / "checkpoints.json")),
            },
        )
        return workspace
