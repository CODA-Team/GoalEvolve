# Toolchain lock

`lock.json` records the observed OpenROAD seed revision, reference-flow revision, and host compiler/CMake/Python versions for the fixed AES artifact. AE-1 checks the shipped inputs; AE-2 records the binary and generated logs. Changing any lock input creates a new artifact claim and requires a new expected manifest.

`environment.yml` is the project-local Conda specification used by `make setup`.
The generated Miniforge installation, Conda environment, Codex CLI, downloads,
and activation script are all ignored below `outputs/toolchain/`. It does not
install OpenROAD or its native build dependencies.

AE-2 and AE-3 deliberately use a separately prepared, version-matched
OpenROAD/ORFS workspace. Activate that workspace in the shell, set
`OPENROAD_EXE` for AE-2, and set a matching `source_root` (or
`GOALEVOLVE_OPENROAD_SEED`) for AE-3. The host workspace owns its compiler,
CMake, native dependencies, shared libraries, and OpenROAD binary.

The release ships all eight contest benchmarks and ASAP7 data under
`third_party/benchmarks/`. The shared source-only OpenROAD p0 lives
at `artifact_evaluation/lineage/openroad_power/p0/source`; verify its manifest
with `python3 artifact_evaluation/verify_openroad_snapshot.py --verify`.
