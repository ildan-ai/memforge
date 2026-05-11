# MemForge quickstart

Single-operator setup for the v0.5+ identity + signing flow. Walks from a fresh `pip install` to a signed operator-registry + a verified agent-session attestation in about 5 minutes.

If you are a single developer running one or more coding agents on your own machine and you do NOT need to share memory with other developers, you can skip directly to the v0.3 / v0.4 memory-format flow (`memory-audit`, `memory-watch`, etc.) without touching any of the v0.5+ commands. The v0.5 identity surface is mandatory only when:

- You are coordinating with at least one other developer's agent writes (multi-operator).
- You want cryptographic provenance on every memory write.
- You are integrating with a tool that requires v0.5+ attestation (the messaging adapter for real-time team sync).

For pure single-developer use, v0.4 frontmatter is still valid; v0.5 readers tag unsigned v0.4 memories as `(v0.4: unsigned)` read-only-untrusted but do not reject them.

## Supported platforms

v0.5.2+ supports macOS, Linux, and native Windows. POSIX implementations enforce file restriction via mode bits (0600 / 0700); Windows implementations enforce via NTFS ACLs (via the built-in `icacls` binary). Both paths satisfy the spec contract "file restricted to current owner" identically.

## Prerequisites

- Python >= 3.10
- GnuPG >= 2.4 on `$PATH`:
  - **macOS:** `brew install gnupg`
  - **Debian / Ubuntu:** `apt install gnupg`
  - **Fedora / RHEL:** `dnf install gnupg2`
  - **Windows (native):** [Gpg4win](https://www.gpg4win.org/) (installer puts `gpg.exe` on PATH)
  - **Windows (WSL):** install GnuPG inside your WSL distribution per the Linux instructions above
- A writable home directory; v0.5 puts the operator-identity at:
  - **macOS / Linux:** `~/.memforge/operator-identity.yaml` and `~/.memforge/recovery-secret.bin`
  - **Windows:** `%USERPROFILE%\.memforge\operator-identity.yaml` and `%USERPROFILE%\.memforge\recovery-secret.bin`

## Step 1: install

```bash
pip install ildan-memforge
memforge --version
# memforge 0.5.2
```

On macOS / Homebrew Python or any system with `EXTERNALLY-MANAGED` Python, prefer:

```bash
brew install pipx
pipx install ildan-memforge
```

On Windows (PowerShell):

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
pipx install ildan-memforge
memforge --version
```

## Step 2: bootstrap the operator identity

```bash
memforge init-operator --name "Your Name" --gen-key
```

Output:

```
operator-UUID: 01923456-7890-7abc-9def-0123456789ab
GPG fingerprint: ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234
identity file:   /home/you/.memforge/operator-identity.yaml  (mode 0600)
```

`--gen-key` creates a fresh Ed25519 keypair in your GPG keyring with no passphrase (so the CLI can sign without prompting). Production deployments should set a passphrase post-generation:

```bash
gpg --edit-key <fingerprint>
> passwd
> save
```

If you already have a GPG key you want to bind to the operator identity:

```bash
memforge init-operator --name "Your Name" --gpg-fingerprint ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234
```

## Step 3: install the recovery secret

The recovery secret is a 32-byte CSPRNG-derived value used to authorize a key-compromise event when your long-lived key is compromised. Its SHA256 is anchored in the signed operator-registry so a swapped recovery-secret file fails closed at adapter startup.

```bash
cd /path/to/memory-root
memforge init-store              # creates the operator-registry first
memforge recovery-init
```

Output:

```
recovery-secret installed at /home/you/.memforge/recovery-secret.bin (mode 0600).
SHA256 anchored in operator-registry: ab12cd34...
```

**CRITICAL.** Back the recovery secret up to OFFLINE physical media (USB key in a safe; printed QR in fireproof storage; encrypted text on dedicated removable media). The recovery procedure requires the secret to be available + uncompromised + physically separated from the signing-key machine. If you lose the recovery-secret AND your long-lived key is compromised, you cannot recover the operator identity for the affected store.

After backing up:

```bash
memforge recovery-backup-confirm --i-have-backed-up-the-secret
```

This sets `recovery.acknowledged_backup_procedure: true` in `~/.memforge/config.yaml`. Adapters refuse v0.5+ writes until this flag is on.

## Step 4: bootstrap a memory-root store

```bash
cd /path/to/your/project-memory-root
memforge init-store
```

Output:

```
operator-registry created at /path/.../memory-root/.memforge/operator-registry.yaml
signed by operator 01923456-7890-7abc-9def-0123456789ab via key ABCD1234....
```

Commit it:

```bash
git add .memforge/operator-registry.yaml
git commit -m "memforge: operator-registry init"
```

## Commit hygiene + signed `memforge:` prefixes

You will see "now run `git add` + `git commit -m 'memforge: ...'`" instructions throughout the reference CLI. The reasons are deliberate and worth knowing up front.

**MemForge does not install git hooks.** The reference CLI writes files to disk + tells you what to commit. Pre-commit hooks, post-commit hooks, server-side hooks: those are your responsibility. The spec keeps it that way so MemForge works on every git host (GitHub, GitLab, Gitea, self-hosted, even bare-bones working trees) without injecting machinery you did not ask for.

**The `memforge:` commit-message prefix is parsed.** Audit, resolve, and revoke walk the commit log and key behavior off the prefix:

- `memforge: operator-registry <verb>` — only the registry file changes; other paths in the same commit are a Tier 2 audit BLOCKER.
- `memforge: resolve <decision_topic>` — the only commit that may transition memories to `status: superseded` for that topic; cross-topic mutations in the same commit are BLOCKER.
- `memforge: revoke <key_id>` / `memforge: revocation-snapshot <hash>` — revocation events; receivers walk these to build the revocation set.
- `memforge: snooze <decision_topic>` / `memforge: config` / `memforge: alias <topic>` — each scoped to its own file. Wrong-prefix-or-out-of-scope commits are BLOCKER.

If you commit registry/resolve/revoke changes under a different prefix, MemForge's audit will not parse them and downstream behavior breaks (memories never supersede, revoked keys keep verifying, etc.). Always use the prefix the CLI suggests.

**Automation: Claude Code users get this for free.** If you drive MemForge through Claude Code's auto-memory system, the PostToolUse `memory-auto-commit.sh` hook commits every Write/Edit inside a memory folder automatically. You still need to honor the `memforge:`-prefix convention for the operations above (registry edits, resolves, revokes, snoozes, config edits, alias edits); those run through the `memforge` CLI which prints the right commit message for you.

**Automation: non-Claude-Code users.** Wire your own. Two reasonable patterns:

- **Auto-commit on file write**, with a fallback prefix like `memory: write <relpath>`. Suitable for casual single-operator use. Make sure your hook does NOT auto-commit changes to `.memforge/operator-registry.yaml`, `.memforge/config.yaml`, or any memory whose change should land under a `memforge: resolve` / `memforge: snooze` / `memforge: alias` / `memforge: revoke` commit — for those, run the `memforge` CLI which prints the correct message and you commit manually (or extend the hook to recognize the staged paths and skip when the CLI is going to handle them).
- **Pre-commit lint hook** that rejects a commit if the staged diff touches a path requiring a Tier 2 prefix (`.memforge/operator-registry.yaml`, files inside an actively-resolving topic, etc.) and the commit message does not match the expected prefix. Lower-friction than auto-commit; catches the common mistake.

**Bottom line.** Commits ARE the audit trail. The diff IS the receipt. Hygiene is the operator's responsibility; MemForge will work cleanly as long as the commits land with the right prefixes.

## Step 5: run the pre-flight checker

`messaging-doctor` runs the v0.5+ fail-closed checklist + reports posture:

```bash
memforge messaging-doctor
```

Expected output for a healthy install:

```
  OK    gpg binary on PATH
  OK    operator-identity readable + 0600 (/home/you/.memforge/operator-identity.yaml)
  OK    recovery-secret 0600 + 0700 parent (/home/you/.memforge/recovery-secret.bin)
  OK    recovery.acknowledged_backup_procedure on file
  OK    operator-registry signature verifies (...)

messaging-doctor: ALL CHECKS PASSED. v0.5.1 posture is healthy.
```

If any check fails, the message tells you what to fix.

## Step 6: issue an agent-session attestation

When you launch a coding-agent session that will write to memory, issue a session attestation that scopes the agent's capabilities:

```bash
memforge attest-agent --memory-root . --adapter-prefix cc
```

Output:

```
agent-session-id: cc-2026-05-10-aaaa1234bbbb5678
agent fingerprint: EEFF1234EEFF1234EEFF1234EEFF1234EEFF1234
attestation file: .memforge/agent-sessions/cc-2026-05-10-aaaa1234bbbb5678.yaml (mode 0600)
issued_at:  2026-05-10T22:00:00Z
expires_at: 2026-05-11T22:00:00Z
capabilities: ['write', 'resolve']
```

The attestation:

- Mints an ephemeral Ed25519 keypair for the agent session.
- Issues a 24-hour-default capability scope of `[write, resolve]` against this memory-root only.
- Signs the whole record with your long-lived operator key.

Commit it:

```bash
git add .memforge/agent-sessions/cc-2026-05-10-aaaa1234bbbb5678.yaml
git commit -m "memforge: attest-agent cc-2026-05-10-aaaa1234bbbb5678"
```

The agent now writes memory under `identity: agent:<operator-uuid>:<agent-session-id>` and signs each write with the ephemeral key. Receivers verify the write signature against the attestation's `agent_pubkey` (NOT the operator-registry, since agent keys are ephemeral and never registry-listed).

To grant broader capability:

```bash
memforge attest-agent --capability revoke --capability registry-edit
```

Adapters SHOULD prompt for confirmation when issuing an attestation with operations beyond `write` + `resolve`.

## Step 7: rotate keys (when needed)

If you ever need to retire your long-lived key (machine replacement, security policy, suspected compromise):

```bash
memforge rotate-key
```

This generates a new Ed25519 keypair, cross-signs both keys, lands a registry update signed by the OLD key, and starts a mandatory 24-hour cool-down during which receivers will reject writes signed by the new key. The cool-down gives you a window to detect an unauthorized rotation before the new key is honored.

After 24 hours, the new key is fully active. Commit the registry update:

```bash
git commit -m "memforge: operator-registry rotate"
```

If your key was actually compromised (not just being retired), use the `memforge: key-compromise` flow (procedure documented at §"Operator key compromise recovery" in the spec; CLI helper is planned for v0.5.3).

## Step 8: revoke a key (when needed)

If a key is compromised or retired and you want every receiver to stop trusting it:

```bash
memforge revoke ABCD1234ABCD1234ABCD1234ABCD1234ABCD1234 \
    --reason "retired after machine replacement" \
    --output /tmp/revoke-msg.txt
git commit --allow-empty -F /tmp/revoke-msg.txt
git push
```

Receivers walking the revocation set will pick up the event on next adapter startup or on next `revoke-cache-refresh` (sparse / shallow-clone deployments). Memories signed by the revoked key continue to verify ONLY for signatures dated BEFORE `revoked_at` (signing-time-aware verification per the spec). To bulk-supersede memories signed under the revoked key:

```bash
memforge memories-by-key ABCD1234...                   # dry-run list first
memforge revoke-memories ABCD1234... --bulk             # apply
```

## What you have at the end

- An operator-UUID bound to a long-lived signing key.
- A recovery-secret with offline backup acknowledged.
- A signed operator-registry in your memory-root.
- A clean `messaging-doctor` posture.
- One agent-session attestation valid for the next 24 hours (re-run `attest-agent` daily, or pass `--lifetime-hours` to extend up to 7 days).

For multi-operator team setup (adding a peer operator to your registry; bootstrapping shared trust), see [`team-bootstrap.md`](./team-bootstrap.md).

## Troubleshooting

- **`gpg --quick-gen-key failed: agent_genkey failed: No agent running`** . macOS gpg-agent socket path collision under `pytest` tmp_path; not an issue in normal CLI use. If you see it elsewhere, restart gpg-agent: `gpgconf --kill gpg-agent`.
- **`operator-registry signature unverifiable`** . adapter HALTS by design. Run `memforge operator-registry verify` to see the specific failure. Common cause: someone edited the registry file outside `memforge operator-registry`.
- **`recovery-secret SHA256 does not match registry-anchored hash`** . the on-disk recovery-secret has been replaced or corrupted. Fail-closed by design; reinstall with `memforge recovery-init --force` OR investigate.
- **`Recovery-secret backup procedure not acknowledged`** . you ran `recovery-init` but not `recovery-backup-confirm`. Back up the secret, then run `memforge recovery-backup-confirm --i-have-backed-up-the-secret`.
