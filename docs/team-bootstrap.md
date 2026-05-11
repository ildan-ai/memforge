# Multi-operator team bootstrap

Walks two developers (Operator A and Operator B) through setting up a shared MemForge store so each can write under their own cryptographic identity and verify the other's writes. Assumes both have completed the single-operator [quickstart](./quickstart.md) on their own machines.

The trust-bootstrap procedure is **operator-mediated out-of-band**: MemForge does not invent a new key-distribution primitive. You exchange GPG fingerprints through a channel you both trust (Signal, phone, in-person, verified email), then add each other to the registry.

## Prerequisites

- Both operators have completed [`quickstart.md`](./quickstart.md) on their own machines.
- Both operators have a populated operator-UUID + a registered long-lived GPG key.
- Both operators have the `recovery-secret` installed + backup acknowledged.
- A shared git repo for the memory-root that both can push to + pull from.

## Pick your transport: git-only or WebSocket?

MemForge v0.5 ships two messaging substrates. The decision is operational, not normative: the same memory verifies identically through either path (see SPEC.md §"Messaging adapters" for the substrate-independent envelope contract).

### git-only (default)

The default adapter writes a signed memory file to disk, commits it to the local repo, and pushes to the shared remote. Other operators pick up new writes on their next `git pull`. The substrate is the same git repo everyone already runs.

**Use git-only when:**

- One operator on the store. The substrate is solo; there is no second reader to race against.
- Two or more operators, but write cadence is slow (a handful of memory writes per day total across the team) and the cost of a missed-cross-claim being caught at next pull (minutes to hours later) is acceptable.
- You want zero infrastructure beyond the git remote you already have.

**Limits:**

- Cross-claim detection is bounded by pull cadence. Operator B will not see Operator A's competing claim until B pulls. Two agents writing under their respective operators within the same pull-cycle window can produce a momentarily-silent overwrite locally, surfaced only at the next pull or audit.
- Local writes that the writer has not yet pushed are invisible to other operators. Standard git eventual-consistency.

This is the right default for almost every team starting out.

### WebSocket

The WebSocket adapter adds a real-time relay between operators. Each operator's MemForge client opens an authenticated `wss://` connection to a relay server; signed memory envelopes broadcast to all connected operators within seconds. The local-first invariant still holds (every write lands in the local git repo first; relay propagation is best-effort), but cross-claim detection latency drops from "next pull" to "next message frame."

**Use WebSocket when:**

- Two or more operators on the store with high write cadence (dozens to hundreds of memory writes per day across the team) where minute-scale conflict-detection windows would let stale-consensus build up between pulls.
- A central audit / governance surface needs to see every memory write as it happens (regulated environments, customer-side agents pushing into an operator-side audit chain).
- You already operate one of the supported substrate options (Kafka, NATS JetStream, Redis Streams, MCP-streaming) and want to layer MemForge on top.

**What it costs:**

- You stand up a WebSocket relay. Reference contract is in SPEC.md §"Messaging adapter contract (WebSocket reference; v0.5.0+)" (TLS-only `wss://` transport, per-operator authentication, circuit-breaker on local-queue exhaustion, signed-checkpoint cadence).
- Every operator's `.memforge/config.yaml` gets a `messaging.adapter: websocket` block plus the relay URL + auth credentials.
- TLS terminates at the relay. The relay is in your trust boundary; the spec contract treats it as untrusted (signed envelopes, per-operator auth, circuit-breakers), but operationally you own its uptime.

WebSocket is opt-in. The git-only path remains a fully-supported v0.5 substrate; pick WebSocket only when the latency math actually matters for your team. The signed-envelope contract (sender_uid + sequence_number + checkpoints) is identical across both, so a team can start on git-only and flip to WebSocket later without re-keying or losing audit continuity.

### When in doubt

Stay on git-only. Reconsider when you can name a specific minute-scale-latency cost that the team is paying.

### Starter material for standing up a relay

When you do decide to bring up WebSocket, the [`examples/websocket/`](../examples/websocket/) folder is a scaffold. It includes a reference `messaging:` config block, a Python probe for verifying relay reachability + auth, and a README that names what's stub vs production. The MemForge reference CLI does not yet ship a WebSocket adapter; teams who need the substrate today implement the relay side themselves OR contract for one. The spec at `spec/SPEC.md` §"Messaging adapter contract (WebSocket reference; v0.5.0+)" is the normative contract.

## Step 1: Operator A initializes the store

Operator A bootstraps the memory-root + commits the initial operator-registry:

```bash
# On Operator A's machine, in the shared memory-root
cd /path/to/shared-memory-root
memforge init-store
git add .memforge/operator-registry.yaml
git commit -m "memforge: operator-registry init"
git push origin main
```

At this point the registry contains exactly one operator (A) and is signed by A's key.

## Step 2: Operator B clones + reads the registry

```bash
# On Operator B's machine
git clone <repo-url> /path/to/local/copy
cd /path/to/local/copy
memforge operator-registry verify
```

Expected output:

```
operator-registry signature OK (1 operator(s) listed).
  - <A's operator-UUID> <A's name> status=active active_keys=1
```

If the verify fails: do not proceed. Investigate the registry state with A out-of-band.

## Step 3: Exchange fingerprints out-of-band

This is the security-critical step. Do NOT rely on the git repo or any in-band channel for this:

- Use Signal / phone / in-person / verified email to exchange the 40-character GPG primary key fingerprints.
- Verify the fingerprint matches the public key material via at least TWO independent channels for adversarial-environment deployments (spec recommends; does not mandate).

A reads:
```bash
# On Operator A's machine
gpg --fingerprint <A's UID>
# pub   ed25519 ...
#       ABCD 1234 ABCD 1234 ABCD 1234 ABCD 1234 ABCD 1234
```

A sends the fingerprint to B (Signal / phone / in-person).

B reads:
```bash
# On Operator B's machine
gpg --fingerprint <B's UID>
```

B sends the fingerprint to A (Signal / phone / in-person).

## Step 4: Operator B imports Operator A's public key (already in their GPG keyring)

Operator A's public key is already in the operator-registry as base64-encoded armored material. Operator B's local GPG keyring may not have it; import it:

```bash
# On Operator B's machine
python3 -c "
import yaml, base64, subprocess
reg = yaml.safe_load(open('.memforge/operator-registry.yaml').read())
for op in reg['operators']:
    for k in op['public_keys']:
        if k.get('status', 'active') == 'active':
            armored = base64.b64decode(k['public_material'])
            subprocess.run(['gpg', '--import'], input=armored, check=True)
            print(f'imported {k[\"key_id\"]}')
"
```

Verify B's keyring now lists A's public key with the expected fingerprint from Step 3:

```bash
gpg --list-keys
```

If the fingerprint does NOT match what A sent out-of-band: stop. Someone is in the middle. Investigate before continuing.

## Step 5: Operator A adds Operator B to the registry

Operator B sends A their operator-UUID + GPG fingerprint (the same fingerprint A verified in Step 3).

Operator A then imports B's public key + adds B to the registry:

```bash
# On Operator A's machine
# (Operator A must have imported B's public key into their GPG keyring; same import flow as Step 4 in reverse)
memforge operator-registry add \
    --operator-uuid <B's-uuid> \
    --operator-name "Operator B" \
    --pubkey-fingerprint <B's-fingerprint>
git add .memforge/operator-registry.yaml
git commit -m "memforge: operator-registry add Operator B"
git push origin main
```

The registry is now signed by A and lists both A and B.

## Step 6: Operator B pulls + verifies

```bash
# On Operator B's machine
git pull origin main
memforge operator-registry verify
```

Expected output:

```
operator-registry signature OK (2 operator(s) listed).
  - <A's operator-UUID> <A's name> status=active active_keys=1
  - <B's operator-UUID> <B's name> status=active active_keys=1
```

Both operators can now write memories under their own identity. Each write is signed by the writer's long-lived key (operator-identity write) or by the writer's agent-session ephemeral key (agent write, with the attestation signed by the operator's long-lived key).

## Step 7: Both operators issue agent-session attestations

Each operator runs `attest-agent` on their own machine for their local coding-agent session:

```bash
# On Operator A's machine
memforge attest-agent --memory-root . --adapter-prefix cc
# attestation file at .memforge/agent-sessions/cc-2026-05-10-aaaa....yaml

# On Operator B's machine (separate session)
memforge attest-agent --memory-root . --adapter-prefix cursor
# attestation file at .memforge/agent-sessions/cursor-2026-05-10-bbbb....yaml
```

Both attestation files commit + push to the shared repo so receivers on either side can verify writes signed under those agent sessions.

## What you have at the end

- A shared memory-root with a signed operator-registry listing both A and B.
- Each operator's long-lived GPG public key in the registry + in the other's local GPG keyring.
- Active agent-session attestations for each operator's coding-agent sessions.
- A path for either operator to verify the other's writes cryptographically without any central authority.

## Adding a third operator (C)

Either A or B can add C to the registry. The same out-of-band fingerprint-exchange discipline applies between the adder and C. Order of trust: A trusts C because A verified C's fingerprint out-of-band; B trusts C because B trusts the registry (signed by A) which now lists C with C's fingerprint anchored.

If B does NOT trust A enough to inherit A's vetting of C, B should do their own out-of-band fingerprint exchange with C as a sanity check before relying on registry entries that A added.

## Removing an operator

If an operator leaves the team or their key is compromised:

```bash
# On any operator who is still trusted
memforge operator-registry remove --operator-uuid <departing-uuid>
git commit -m "memforge: operator-registry remove <name>"
git push origin main
```

The departing operator's entry is marked `status: superseded`. Their historical writes remain verifiable for signatures dated before the removal commit (signing-time-aware verification). Future writes signed by their key will fail verification at the registry-lookup step.

If their key was compromised (not just retired), additionally run `memforge revoke <key_id> --reason "compromise"` so receivers stop trusting the key as of `revoked_at`, regardless of the registry entry's status.

## Key rotation in a multi-operator setup

When an operator rotates their long-lived key:

```bash
memforge rotate-key
```

This generates a new keypair, cross-signs both, lands the registry update signed by the OLD key, and starts the 24-hour cool-down. During the cool-down, receivers reject writes signed by the new key. After 24 hours, the new key is fully active.

The cool-down is the protection against a compromised-key-as-rotation-attack: an attacker who compromised the old key cannot use it to rotate to a new key under their control and immediately sign with the new key . they have to wait 24 hours, during which the legitimate operator detects the unauthorized rotation and initiates `memforge: key-compromise`.

## Troubleshooting

- **`operator <uuid> already present in registry`** . you tried to `add` an operator who's already there. Use `rotate-key` or `fresh-start` to update their keys instead.
- **`fingerprint <fpr> not found in local gpg secret keyring`** . you tried to `init-operator --gpg-fingerprint` with a key you have only the public half of. Import the secret half first or use `--gen-key`.
- **Verify fails after pull with "registry signature did not verify"** . possible registry corruption or unauthorized edit. Compare the current registry against the previous git revision (`git show HEAD~1:.memforge/operator-registry.yaml`) and reconstruct from the last known-good state with the affected operator out-of-band.
- **Adapter HALTs with `revocation history divergence`** . sparse / shallow-clone mode detected a non-fast-forward divergence in the remote ref. Either switch to full-clone mode OR investigate the remote with the other operators.
