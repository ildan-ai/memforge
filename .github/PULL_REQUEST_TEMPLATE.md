<!--
  Thanks for contributing. Every commit must be signed off (`git commit -s`);
  CI fails the PR otherwise. See CONTRIBUTING.md.
-->

## Summary

<!-- One or two sentences. What does this change do, and why? -->

## Scope

- [ ] `spec/` (cross-agent on-disk format change; requires spec version bump)
- [ ] `tools/` (cross-agent shared tooling)
- [ ] `adapters/<agent>/` (single-agent integration)
- [ ] `src/memforge/` (Python library API)
- [ ] `tests/` or CI
- [ ] documentation only

## Gates

- [ ] **Agent-neutrality:** if this touches `spec/` or `tools/`, no single agent (Claude Code, Cursor, Aider, Codex, Copilot, etc.) is referenced in the contract surface. Agent-specific work lives in `adapters/<agent>/`.
- [ ] **Spec version:** if `spec/SPEC.md` or `spec/taxonomy.yaml` changed, `spec/VERSION` is bumped accordingly (SemVer track).
- [ ] **CHANGELOG:** an entry exists under `## [Unreleased]` describing the user-visible change.
- [ ] **DCO:** every commit carries a `Signed-off-by:` line matching its author (`git commit -s`).
- [ ] **Tests:** `pytest` passes locally. New behavior has matching tests.
- [ ] **Audit clean:** `tools/memory-audit --strict` passes against the test fixtures.
- [ ] **No secrets / PII:** no credentials, tokens, or personal data in the diff or in test fixtures.
- [ ] **License compatibility:** any new runtime dependency is permissively licensed (MIT / Apache-2.0 / BSD); no GPL/AGPL/SSPL/BSL transitives.
- [ ] **Sensitive content excluded:** the diff contains no memory files with an `access:` label narrower than `public`, no `sensitivity:` higher than `internal`, and nothing your project's own scrubber policy would block from a public repo.

## Linked Issue

<!-- e.g. "Closes #123". -->

## Reviewer notes

<!-- Anything the reviewer should pay particular attention to: a tricky edge case, a known limitation, a deferred follow-up, etc. -->
