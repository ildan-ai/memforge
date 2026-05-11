# MemForge WebSocket examples (scaffold)

Starter material for operators standing up the WebSocket messaging substrate. **This is a scaffold, NOT a deployable relay.** The MemForge spec at `spec/SPEC.md` §"Messaging adapter contract (WebSocket reference; v0.5.0+)" defines the normative relay-side contract; this folder helps you bring up something that can interoperate with it.

## State of the substrate

- The MemForge spec (`spec/SPEC.md`) defines the WebSocket relay contract normatively: TLS-only (`wss://`), per-operator strong auth (mTLS OR per-operator bearer tokens; static / shared / no-auth rejected), signed-envelope schema, sender-uid + sequence + signed checkpoints, circuit-breaker on local-queue exhaustion, single-server-only in v0.5.0 (multi-server deferred to v0.5.x / v0.6+).
- The `ildan-memforge` reference CLI **does NOT yet ship a WebSocket adapter** in `src/`. Today's reference implementation operates over the git-only substrate (default; works out of the box for solo + low-cadence teams).
- Teams who need real-time cross-operator sync (see `docs/team-bootstrap.md` §"Pick your transport: git-only or WebSocket?" for the decision-framing) implement the relay side themselves OR contract for one. Your relay must conform to the spec contract; mismatches will be visible to receivers in the form of envelope-validation failures.

## What's in this folder

### `config.example.yaml`

A reference shape for the `.memforge/config.yaml` `messaging:` block. Forward-looking: this is the config schema a future MemForge client adapter will read. Today, treat it as the input contract you point your operators at when documenting your relay deployment.

### `probe.py`

A minimal Python probe that opens a `wss://` connection to your relay, authenticates with a per-operator bearer token, sends a single signed-envelope test message, and prints the echo. Use it to:

- Verify the relay is reachable + TLS handshake succeeds.
- Verify per-operator authentication is wired (the relay rejects a missing or mis-signed token).
- Verify the envelope round-trip (relay echoes the message back to other connected operators).

It is NOT a complete MemForge client. It does NOT implement sender-uid + monotonic sequence + signed checkpoint cadence (the spec mandates these for any real adapter; the reference CLI's eventual WebSocket adapter will). It does NOT sign envelopes against the operator's actual GPG key (probe.py uses a placeholder signature for round-trip verification only; production envelopes must carry the operator's real signature per §"Cryptographic attribution").

## When you have a real relay

The probe is the floor. Above it: a compliant relay implementation is materially harder. The spec is the contract, but a sketch of what compliance demands:

- TLS termination (Let's Encrypt or your CA of choice; mTLS option requires you to issue + manage per-operator client certs).
- Operator-registry lookup at connection time (relay fetches the registry from the canonical memory-root git remote OR consumes a cached signed copy; rejects connections from operator-UUIDs not in `status: active`).
- Signed envelope validation (relay verifies `signature` against the registered operator's current key; rejects on mismatch).
- Broadcast fan-out (single-server in v0.5.0; relay holds an in-memory map of connected operator-UUIDs and writes each accepted envelope to every other connection).
- Circuit-breaker discipline (per-connection back-pressure; client-side disk-queue exhaustion is the spec's hard-stop, not relay-side).
- Audit logging (relay logs every accepted envelope's identity + signing_time + sender_uid; logs become part of the team's audit trail).

When you have that running: open an Issue on the repo with what you built and what you learned. The next layer of work is a reference relay in the repo; that work is gated on real-world feedback from operators who have stood one up.

## Disclaimers

- This scaffold has not been independently security-reviewed. It is a starting point for honest experimentation, not a production runtime.
- The probe sends placeholder signatures and a placeholder operator-UUID. Replace both before pointing it at any non-localhost relay.
