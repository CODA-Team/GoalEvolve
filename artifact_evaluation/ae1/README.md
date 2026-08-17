# AE1: artifact setup check

AE1 checks that the published benchmarks, frozen source snapshots, manifests,
portable Tcl files, and official checkers are present and internally
consistent.

Run it from the repository root:

```bash
PYTHONPATH=. python3 artifact_evaluation/ae1/run_ae1.py \
  --artifact aes_cipher_top_student_code
```

The `--artifact` value is one of the generic `<design>_student_code` entries
in `artifact_evaluation/release_manifest.json`.
