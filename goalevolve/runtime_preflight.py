"""Runtime checks that must happen before an AST-planned campaign mutates state."""

from __future__ import annotations

import sys


_MINIMUM_PYTHON = (3, 11)


def require_ast_graph_runtime(*, operation: str) -> None:
    """Fail early when the active interpreter cannot build an AST graph.

    Repository-graph caches can postpone parsing for several rounds.  They
    must never postpone discovery that the process which will eventually parse
    a changed C++ file is an unsupported or incomplete Python environment.
    """

    failures: list[str] = []
    version = sys.version.split()[0]
    if sys.version_info < _MINIMUM_PYTHON:
        failures.append(
            "GoalEvolve requires Python >= 3.11 for ast_graph, "
            f"but this process is Python {version}."
        )
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_cpp

        # Import success alone is insufficient: mixed site-packages can leave
        # tree-sitter and tree-sitter-cpp at incompatible binary/API versions.
        Parser(Language(tree_sitter_cpp.language()))
    except Exception as exc:  # import and ABI errors are both environment faults
        failures.append(
            "tree-sitter/tree-sitter-cpp is unavailable or incompatible in "
            f"this interpreter ({type(exc).__name__}: {exc})."
        )
    if not failures:
        return

    details = " ".join(failures)
    raise RuntimeError(
        f"AST repository-graph preflight failed before {operation}. {details}\n"
        f"Active interpreter: {sys.executable} (Python {version}).\n"
        "Do not use a Python 3.10 environment such as mlcad-py310 for an "
        "AST-planned campaign. Create or select one Python 3.11+ environment, then install "
        "GoalEvolve into that exact interpreter, for example:\n"
        "  python3.11 -m venv .venv-goalevolve\n"
        "  .venv-goalevolve/bin/python -m pip install -e .\n"
        "  .venv-goalevolve/bin/python -m goalevolve.cli p0 run ..."
    )
