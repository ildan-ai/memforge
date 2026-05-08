# Sensitivity conformance fixtures

Spec: `spec/SPEC.md` §"Sensitivity enforcement (v0.4.0+)" → "Conformance fixtures".

Each scenario is a directory with:

- `expected.json` — assertions about which violations the tooling should
  emit when run against the scenario input.
- An `input/` directory OR a reference into `shared_input/` (the four
  export-tier scenarios reuse a single 4-level memory folder so that a
  drift in fixture content between tiers is impossible).

The harness in `tests/test_conformance_sensitivity.py` parametrizes over
every scenario directory, runs the configured tool with the configured
args, and asserts the actual output matches the expectations.

## Scenario list

| Scenario | Tool | Asserts |
| --- | --- | --- |
| `export_tier_public` | `memory-audit --export-tier=public` | files at internal/restricted/privileged are flagged BLOCKER |
| `export_tier_internal` | `memory-audit --export-tier=internal` | files at restricted/privileged are flagged BLOCKER |
| `export_tier_restricted` | `memory-audit --export-tier=restricted` | files at privileged are flagged BLOCKER |
| `export_tier_privileged` | `memory-audit --export-tier=privileged` | no export-tier violations |
| `label_mismatch_blocked` | `memory-dlp-scan --paths <input>` | a public-labeled file containing restricted-tier content emits `sensitivity_label_mismatch` BLOCKER |

## Adapter authors

Run the conformance suite against your secure-mode adapter to confirm it
honors the spec's sensitivity rules:

```bash
pytest tests/conformance/sensitivity/ -v
```

If your adapter targets a non-secure-mode profile you MAY skip
individual scenarios with `pytest.skip("non-secure-mode")` and a documented
rationale, but spec-conformance claims are forfeit for the skipped
scenarios.
