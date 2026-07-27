# Shared OpenROAD p0 source

This is the immutable OpenROAD source baseline shared by every shipped design
profile. It is a source-only snapshot: `.git`, `build`, and
`build_power` are deliberately absent. GoalEvolve creates private build trees
under each campaign output, so a design profile is reproducible without
depending on an external OpenROAD checkout or a prebuilt binary.

`source_manifest.json` identifies the snapshot by a full-tree content digest.
The source origin had uncommitted changes at capture time, therefore the Git
commit is provenance context only and the content digest is authoritative.
Recompute it with:

```bash
python3 artifact_evaluation/verify_openroad_snapshot.py --verify
```

The digest covers normalized relative paths, regular-file sizes and contents,
directory names, and symlink targets, while intentionally excluding file
ownership, permissions, and timestamps.

The source is shared because the evolved program is OpenROAD itself; design
specific inputs belong under
`third_party/benchmarks/benchmarks/<design>/`.
