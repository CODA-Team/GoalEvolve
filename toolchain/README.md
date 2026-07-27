# Toolchain lock

`lock.json` records the observed OpenROAD seed revision, MLCAD contest revision, and host compiler/CMake/Python versions for the fixed AES artifact. AE-1 checks the shipped inputs; AE-2 records the binary and generated logs. Changing any lock input creates a new artifact claim and requires a new expected manifest.

The release ships all eight contest benchmarks and ASAP7 data under
`third_party/mlcad2026_benchmarks/`. The shared source-only OpenROAD p0 lives
at `artifact_evaluation/lineage/openroad_power/p0/source`; verify its manifest
with `python3 artifact_evaluation/verify_openroad_snapshot.py --verify`.
