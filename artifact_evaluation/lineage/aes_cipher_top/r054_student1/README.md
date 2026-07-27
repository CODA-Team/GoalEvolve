# Frozen source artifact: AES R54 Student 1

`source/` is the complete source snapshot promoted as `round_054:student_1`, not a build cache and not a symlink into `GoalEvolve_v2`. It is the authoritative AE-2 input.

The release also retains the R54 implementation diff under `../../../../expected/aes_cipher_top/r054_student1/implementation.diff`. The frozen tree is used instead of replaying an incomplete patch history because AE-2 must verify the exact reported source result without invoking an LLM or relying on an earlier campaign runtime directory.

Build outputs must be written under `outputs/ae2/`; do not add build trees inside `source/`.
