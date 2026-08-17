# AE2: fixed artifact replay

AE2 replays one published frozen OpenROAD source artifact and compares its
official evidence. It does not run Teacher/Student evolution or use API
credentials.

Check the host tool before a replay:

```bash
PYTHONPATH=. python3 artifact_evaluation/ae2/run_ae2.py preflight \
  --artifact aes_cipher_top_student_code
```

Replay after the per-artifact OpenROAD build is available:

```bash
PYTHONPATH=. python3 artifact_evaluation/ae2/run_ae2.py replay \
  --artifact aes_cipher_top_student_code --rebuild
```

Each released source and evidence path uses the generic
`<design>/student_code` alias. The replay resolves those paths through the
release manifest rather than through a machine-specific campaign directory.
