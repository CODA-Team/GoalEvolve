# AES cipher top experiments

`evolve.json` is the only AES AE-3 launch profile. It inherits the shared
four-Student configuration from `experiments/ae3_base.json`, starts from the
frozen R54 source, and defaults to `outputs/ae3/aes_cipher_top/`.

Run `PYTHONPATH=. python3 -m goalevolve.cli run --config
experiments/aes_cipher_top/evolve.json --rounds 10`. Repeating the command
resumes its state root and appends rounds. `archive/` holds historical records
and an old smoke profile for provenance only; none are launch profiles.

The frozen artifact target is TNS ≤ 12 ns, dynamic power ≤ 350B pW, and leakage ≤ 35M pW. These are decision metrics. Runtime, SPPA, and Sfinal remain observers.
