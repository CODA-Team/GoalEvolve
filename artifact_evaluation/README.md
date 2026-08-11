# Artifact evaluation

This directory separates a deterministic release claim from a stochastic LLM experiment.

## AE-1: environment and interface preflight

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae1
```

AE-1 verifies the release manifest, all eight frozen sources and portable Tcl
files, the official parser/checker, all eight shipped design inputs
(`.def(.gz)`, Verilog, SDC, and metrics), ASAP7 files, the shared OpenROAD p0
manifest/content digest, and Python. The JSON result is the authoritative
preflight record.

## AE-2: deterministic fixed-artifact replay

```bash
export OPENROAD_EXE=/path/to/prepared/OpenROAD/bin/openroad
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2-preflight \
  --artifact aes_r58_student1 --openroad "$OPENROAD_EXE" --verbose
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2 \
  --artifact aes_r58_student1 --rebuild --jobs 8 --verbose
```

This command never starts Teacher, Student, retrieval, Codex, or an API call.
It stages and builds the selected artifact's frozen source, runs that binary
with the path-normalized Tcl into `outputs/ae2/`, parses metrics, executes the
official 4/4 checker, and compares the three decision metrics with the
tolerances in `release_manifest.json`. A later run may omit `--rebuild` to
reuse only that artifact's build cache.

Before replay, `ae2-preflight` with `OPENROAD_EXE` verifies that the active
host environment can launch OpenROAD. The external executable is a diagnostic
only: formal AE-2 never uses it in place of the selected artifact binary.
`--verbose` streams flow logs to the terminal; all logs remain recorded under
`outputs/ae2/`.

The selected modes are `power_then_timing`, NVDLA-A `power_only`, and NVDLA-C
`timing_only`; none is detailed routing. A passing AE-2 report proves that the
released source artifact, benchmark, checker, and flow produce the reported
result on the declared environment; it does not prove a fresh LLM run will
rediscover the same patch. See
[AE2_SELECTIONS.md](AE2_SELECTIONS.md) for the eight source/Tcl/QoR records.

## AE-3: fresh evolution

Create `config/credentials/goalevolve_codex.env` from
`config/credentials/goalevolve_codex.env.example`, with mode `0600`; it is
ignored by Git and must never be committed:

```dotenv
GOALEVOLVE_OPENAI_API_KEY=<your independent key>
GOALEVOLVE_PROVIDER_NAME=OpenAI
GOALEVOLVE_BASE_URL=https://api.openai.com/v1
GOALEVOLVE_WIRE_API=responses
GOALEVOLVE_REQUIRES_AUTH=true
GOALEVOLVE_PREFERRED_AUTH_METHOD=apikey
GOALEVOLVE_DISABLE_RESPONSE_STORAGE=true
GOALEVOLVE_NETWORK_ACCESS=true
GOALEVOLVE_GOALS=true
GOALEVOLVE_WSL_ACK=true
```

The API-key file is the sole credential source. Worker homes are generated under `outputs/` and do not inherit `~/.codex`. The project-wide Teacher and Student model policy is committed in `config/codex.json`; it currently uses `gpt-5.6-terra` with `xhigh` reasoning.

```bash
# Four independent Students for a normal fresh campaign.
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m goalevolve.cli run \
  --config experiments/aes_cipher_top/evolve.json --rounds 10
```

An AE-3 pass means the key, Codex invocation, source editing, build, flow, official checks, evidence, and campaign recording complete. It must report token use and verified QoR, but cannot require a specific QoR because model output and search scheduling are non-deterministic.

## Evidence inventory

Each `expected/<design>/<selection>/` directory contains the candidate/evidence
records, metrics, original and portable Tcl, and source manifest. The matching
`lineage/<design>/<selection>/source/` directory contains that artifact's full
immutable OpenROAD source. Generated ODBs, builds, flows, and API sessions
belong under `outputs/`, not this release evidence.
