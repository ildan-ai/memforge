---
uid: mem-2026-05-08-public-with-arn
name: Public-labeled rule with restricted-tier body
description: deliberately mislabeled fixture; declared public yet body has ARN
type: feedback
tier: portable
tags: [topic:conformance]
owner: conformance-fixture
status: active
created: 2026-05-08
sensitivity: public
---

When deploying the platform stack, the role
`arn:aws:iam::123456789012:role/conformance-fixture-role` is assumed by the
runner. The ARN itself is not a credential, but it identifies internal
infrastructure and so must not appear in a public-labeled memory.

**Why:** the conformance fixture intentionally puts an AWS ARN in a
public-labeled file. The DLP cross-check MUST flag this as
`sensitivity_label_mismatch` BLOCKER.

**How to apply:** the `memory-dlp-scan --paths <this file>` invocation
should fail with a BLOCKER. If it does not, the cross-check is broken.
