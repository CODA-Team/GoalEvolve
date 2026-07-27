# Official metric parser

`parse_log.py` is the unmodified MLCAD 2026 contest parser vendored from the official evaluation package. `contest2026.py` runs it after the post-route OpenROAD flow and reads its CSV output to populate the configured frozen metrics (for example TNS, dynamic power, and leakage power in the active AES profile).

It is not a GoalEvolve planner, normalized-gap formula, Sfinal/SPPA formula, or promotion policy. Sfinal/SPPA are computed separately by `goalevolve/evaluation/sfinal.py` as observer-only comparison artifacts.
