# Official MLCAD 2026 4/4 validity checker

This directory is the official contest validity-check snapshot. `def_validity_check.py` orchestrates the four checks; `flipflop_check.py` implements the DFF/clock check; `OpenROAD_utils.tcl` writes the required node/net exports; and the CSV lists equivalent movable-cell families.

`contest2026.py` uses these files directly after the post-route artifact is exported. A candidate passes its `lec` evidence slot only when this checker returns success and prints `SUMMARY: 4/4 checks passed`. This gate complements, but does not replace, private build/flow success, parsed frozen QoR metrics, and zero-DRV validation.
