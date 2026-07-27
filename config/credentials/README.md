# Codex credentials

`goalevolve_codex.env` is the only project credential input for AE-3.
It is intentionally Git-ignored. Create it from
`goalevolve_codex.env.example`, replace the API key and provider fields, then
run `chmod 600 config/credentials/goalevolve_codex.env`.

Every Teacher and Student receives a newly generated private Codex home under
the campaign `outputs/` directory. The worker homes are not credential
sources: each turn reloads this project dotenv, so key rotation takes effect
without reusing `~/.codex` or a stale worker configuration.

Models, reasoning effort, retry behavior, and timeouts do not belong here.
They are shared across every design in the committed `../codex.json` file.
