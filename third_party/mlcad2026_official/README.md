# Pinned MLCAD 2026 official checker snapshot

Source: `MLCAD26-Contest-Scripts-Benchmarks/evaluation`.

The minimal v2 third-party set is:

- `validity_check/def_validity_check.py`: official four-check orchestration.
- `validity_check/flipflop_check.py`: DFF count and clock-connectivity check.
- `validity_check/OpenROAD_utils.tcl`: exports `node.csv`/`nets.csv` from the candidate OpenROAD database.
- `validity_check/asap7_equivalent_cell_list.csv`: official equivalent-cell table.
- `evaluation/parse_log.py`: official evaluation-log metric parser.

`def_validity_check.py` checks fixed physical cells, macros, I/O locations, and flip-flop integrity. v2 marks the candidate `lec` check as passed only when the process returns zero and emits `SUMMARY: 4/4 checks passed`. This is contest structural validity, not a general Boolean-equivalence proof.

These files are vendored without algorithmic modification. Their upstream usage is documented by the contest package. Do not modify this directory to change evolution behavior.
