# Artifact evaluation

This directory separates a deterministic release claim from a stochastic LLM experiment.

## AE-1: environment and interface preflight

```bash
source outputs/toolchain/activate.sh
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae1
```

AE-1 verifies the release manifest, frozen AES source, portable Tcl, official
parser/checker, all eight shipped design inputs (`.def(.gz)`, Verilog, SDC,
and metrics), ASAP7 files, the shared OpenROAD p0 manifest/content digest,
and Python. The JSON result is the authoritative preflight record.

## AE-2: deterministic fixed-artifact replay

```bash
export OPENROAD_EXE=/path/to/prepared/OpenROAD/bin/openroad
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2-preflight \
  --artifact aes_r54_student1 --openroad "$OPENROAD_EXE" --verbose
PYTHONPATH=. "$GOALEVOLVE_CONDA_PREFIX/bin/python" -m artifact_evaluation.runner ae2 \
  --artifact aes_r54_student1 --openroad "$OPENROAD_EXE" --verbose
```

This command never starts Teacher, Student, retrieval, Codex, or an API call. It runs the path-normalized R54 Tcl with a prepared, version-matched host OpenROAD executable into `outputs/ae2/`, parses metrics, executes the official 4/4 checker, and compares the three decision metrics with the tolerances in `release_manifest.json`.

Before replay, run `ae2-preflight` with the same `OPENROAD_EXE`; it verifies
that executable starts under the active host environment. `--verbose` streams
flow logs to the terminal; all logs remain recorded under `outputs/ae2/`.

The fixed claim is post-route `global_route + estimate_parasitics`, not detailed routing. A passing AE-2 report proves that the released source artifact, benchmark, checker, and flow produce the reported result on the declared environment; it does not prove a fresh LLM run will rediscover the same patch.

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

`expected/aes_cipher_top/r054_student1/` contains the candidate/evidence/hypothesis records, metrics, observer scores, original and portable Tcl, source diff, and source manifest. `lineage/` contains the full immutable source. Generated ODBs, builds, flows, and API sessions belong under `outputs/`, not this release evidence.

For the package-reorganization comparison, run
`python3 artifact_evaluation/audit_migration.py --reference ../GoalEvolve_v2 --format markdown`.
The resulting mapping and deliberate portable differences are documented in
`artifact_evaluation/MIGRATION_AUDIT.md`.

## Local validation snapshot

The following generated evidence was produced on 2026-07-27 and remains under ignored `outputs/`:

| Track | Evidence path | Result |
|---|---|---|
| AE-1 | command JSON stdout | passed; all shipped paths and Python interface present |
| AE-2 | `outputs/ae2/aes_r54_student1/report/ae2_report.json` | passed with a version-matched OpenROAD executable; TNS `15.79 ns`, dynamic `335.9714B pW`, leakage `28.6M pW`, official 4/4 pass |
| AE-3 | local historical smoke record | one real Teacher/Student round completed; candidate activated and passed 4/4, then was refuted for no QoR improvement |

The historical smoke token total was `1,682,734`. Generated API homes and `auth.json` files are ignored and must not be copied into release evidence.
