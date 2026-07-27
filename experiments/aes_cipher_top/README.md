# AES cipher top experiments

`ae3_smoke.json` is the one-Student, one-round real connectivity workflow. It starts from the fixed R54 artifact, uses local project credentials, and writes only to `outputs/ae3/aes_cipher_top_smoke/`.

`evolve.json` extends that profile to four Students and removes the one-round cap. `power_target.json`, `historical_qor.json`, and `finalflow.json` are retained historical campaign records; their original runtime references are provenance, not portable launch profiles.

The frozen artifact target is TNS ≤ 12 ns, dynamic power ≤ 350B pW, and leakage ≤ 35M pW. These are decision metrics. Runtime, SPPA, and Sfinal remain observers.
