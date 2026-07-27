# Tests

Run the deterministic core suite from the project root:

```bash
PYTHONPATH=. python3 -m unittest tests.unit.test_core -q
```

`unit/` contains deterministic controller and official-check regressions. `integration/` is reserved for an installed OpenROAD flow. `artifact/` validates release manifests and fixed-artifact interfaces without requiring an LLM. `pytest` may also be used where installed.

The official-check test skips only when official benchmark data is absent. It does not rebuild OpenROAD. A real build/flow/4-of-4 validation remains an integration campaign operation, not a unit-test substitute.
