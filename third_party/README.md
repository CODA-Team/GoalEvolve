# `third_party/`: pinned external checker implementations

This directory contains only third-party or official checker code that is required at runtime and must remain pinned. It is outside the GoalEvolve source-search space. Upgrade it only as an explicit third-party update with recorded upstream provenance.

The current `mlcad2026_official/` snapshot is mechanically imported from the local MLCAD 2026 Contest Scripts & Benchmarks `evaluation/validity_check` and `evaluation/parse_log.py`. It exports/checks candidate node, net, Verilog, and metric artifacts. It does not define GoalEvolve's frozen QoR gap, power-first staging, retrieval, or promotion semantics.
