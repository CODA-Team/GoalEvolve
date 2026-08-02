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

## Repository graph

`repository_graph/` is the checked-in tree-sitter C++ graph for this exact P0
content digest. It covers production C++ implementation and header files under
editable `src/rsz` and `src/rmp`, excluding module `test/tests` directories,
and contains `manifest.json`, `graph.json`, and `doc_cards.json`. The graph is
a source-localization artifact, not a build result or an evaluation result.

The current graph schema is `goalevolve.repository_graph.v6`. Every file and
symbol card carries the exact graph `source_hash`. Function cards
include both `qualified_name` and the parser-preserved `declarator`. A short
anchor is admitted only when unique in its file; a function overload must use
the full qualified declarator. In Teacher Markdown, separate multiple source
anchors with semicolons, not commas.

Teacher prompts receive a bounded induced subgraph rather than the full graph:
selected high-score seeds reserve part of the fixed card budget for one-hop AST
callers/callees, and selected cards are closed over their selected file nodes.
Card relation lists reference only nodes in that packet, and `edges` contains
only AST edges whose endpoints are both present. `simplification` records the
seed, neighbour, backfill, selected, and omitted counts. The complete graph
remains available in this directory.

Regenerate it after intentionally replacing the P0 source snapshot:

```bash
PYTHONPATH=. python3 -c \
  'from goalevolve.planning.repository_graph import RepositoryGraphIndex; RepositoryGraphIndex(state_root=__import__("pathlib").Path("outputs/repository-graph-refresh")).build_p0()'
```

The manifest's `source_hash` and `base_source_hash` must both equal
`source_manifest.json` `content_sha256`. Changed campaign parents keep their
own incremental graphs below the campaign state root and never modify this P0
artifact.
