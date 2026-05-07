<!--
  External PRs are currently paused.

  MemForge is a single-corporate-steward open-core project. The Contributor License Agreement (CLA)
  infrastructure is counsel-blocked. Until the CLA flow is live, external pull
  requests cannot be merged.

  Please open an Issue or a Discussion instead. The CLA flow will land in a v0.3.x patch release once
  counsel clears the template; this notice will go away at that point.

  This template is in place for the internal team and for the future post-CLA contributor flow.
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
- [ ] **Tests:** `pytest` passes locally. New behavior has matching tests.
- [ ] **Audit clean:** `tools/memory-audit --strict` passes against the test fixtures.
- [ ] **No secrets / PII:** no credentials, tokens, or personal data in the diff or in test fixtures.
- [ ] **License compatibility:** any new runtime dependency is permissively licensed (MIT / Apache-2.0 / BSD); no GPL/AGPL/SSPL/BSL transitives.
- [ ] **Sensitive content excluded:** the diff contains no memory files with an `access:` label narrower than `public`, no `sensitivity:` higher than `internal`, and nothing your project's own scrubber policy would block from a public repo.

## Linked Issue / Discussion

<!-- e.g. "Closes #123" or "Continues discussion in #456". -->

## Reviewer notes

<!-- Anything the reviewer should pay particular attention to: a tricky edge case, a known limitation, a deferred follow-up, etc. -->
