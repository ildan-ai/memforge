#!/usr/bin/env python3
"""MemForge WebSocket relay probe.

A diagnostic that verifies a relay is reachable + minimally compliant
with the MemForge spec (`spec/SPEC.md` §"Messaging adapter contract").

This is NOT a complete MemForge client. It does NOT implement:
- The full sender-uid + monotonic sequence + signed checkpoint
  machinery the spec mandates for production adapters.
- Real GPG signing against the operator's long-lived key (uses
  a placeholder signature for round-trip verification only).
- Reconnection / circuit-breaker logic.

Use it to verify:
- TLS handshake succeeds against your relay's wss:// endpoint.
- Per-operator bearer-token authentication is wired (the relay
  rejects a missing or mis-signed token, and accepts a valid one).
- The relay echoes the envelope back to other connected operators.

Requirements:
    pip install websockets

Usage (single-operator round-trip test):
    export MEMFORGE_RELAY_BEARER_TOKEN='<your operator JWT>'
    python3 probe.py wss://memforge-relay.example.com/v0.5 \\
        --operator-uuid 01923456-7890-7abc-9def-0123456789ab

Usage (localhost relay during development):
    export MEMFORGE_ALLOW_WS_LOCALHOST_ONLY=1
    python3 probe.py ws://127.0.0.1:8000/v0.5 \\
        --operator-uuid 01923456-7890-7abc-9def-0123456789ab \\
        --insecure-localhost
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import websockets
    from websockets.exceptions import (
        ConnectionClosed,
        InvalidStatus,
        WebSocketException,
    )
except ImportError:
    sys.exit(
        "probe.py requires the 'websockets' package.\n"
        "Install with: pip install websockets"
    )


def _validate_url(url: str, insecure_localhost: bool) -> None:
    """Enforce the spec's transport rules client-side before connecting."""
    parsed = urlparse(url)
    if parsed.scheme == "wss":
        return
    if parsed.scheme == "ws":
        if not insecure_localhost:
            sys.exit(
                "MemForge spec requires wss:// (TLS). Plain ws:// is rejected.\n"
                "For localhost-only development, pass --insecure-localhost.\n"
                "Setting MEMFORGE_ALLOW_WS_LOCALHOST_ONLY=1 is the spec env-var "
                "convention; this probe accepts either signal."
            )
        host = (parsed.hostname or "").lower()
        if host not in {"127.0.0.1", "::1", "localhost"}:
            sys.exit(
                f"--insecure-localhost was passed but host '{host}' is not a "
                "loopback address. Spec requires loopback for ws:// usage."
            )
        return
    sys.exit(f"Unsupported scheme '{parsed.scheme}'. Use wss:// (or ws:// loopback).")


def _build_placeholder_envelope(operator_uuid: str) -> dict:
    """Construct a probe envelope shaped like a v0.5 memory write.

    Signature is a placeholder; the relay should still echo if it accepts
    the connection's bearer auth. A spec-compliant relay would also
    verify the signature against the operator-registry. Configure your
    relay to allow probe traffic during diagnostics OR sign with a real
    test key.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = (
        "MemForge probe envelope. If you see this in a memory file, "
        "your probe leaked into production traffic. Investigate."
    )
    placeholder_sig = hashlib.sha256(
        (body + operator_uuid + now).encode("utf-8")
    ).hexdigest()
    sender_uid = f"{operator_uuid}:{secrets.token_hex(32)}"
    return {
        "envelope_kind": "probe",
        "memory_body": body,
        "identity": {
            "operator_uuid": operator_uuid,
            "agent_session_id": "probe-session-noop",
        },
        "signature": {
            "algorithm": "placeholder-sha256",
            "signing_time": now,
            "value": placeholder_sig,
        },
        "sender_uid": sender_uid,
        "sequence_number": 1,
        "probe_nonce": secrets.token_hex(16),
        "probe_sent_at_unix": int(time.time()),
    }


async def _probe(
    url: str,
    operator_uuid: str,
    insecure_localhost: bool,
    timeout: float,
) -> int:
    _validate_url(url, insecure_localhost)

    token = os.environ.get("MEMFORGE_RELAY_BEARER_TOKEN")
    if not token:
        print(
            "WARNING: MEMFORGE_RELAY_BEARER_TOKEN is not set. The probe will "
            "attempt to connect without an auth header; a spec-compliant "
            "relay MUST reject the connection.",
            file=sys.stderr,
        )

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    envelope = _build_placeholder_envelope(operator_uuid)
    print(f"-> Connecting to {url} as operator {operator_uuid}")
    print(f"-> Auth: {'bearer (token in MEMFORGE_RELAY_BEARER_TOKEN)' if token else 'NONE (expecting reject)'}")
    print(f"-> Sending probe envelope (nonce={envelope['probe_nonce']})")

    try:
        async with websockets.connect(
            url,
            additional_headers=headers if headers else None,
            open_timeout=timeout,
            close_timeout=timeout,
        ) as ws:
            await ws.send(json.dumps(envelope))
            print("-> Envelope sent; waiting for echo (timeout: %.1fs)" % timeout)
            try:
                reply_raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                print(
                    "<- TIMEOUT: relay accepted the connection but did not echo "
                    "the envelope within the window. The relay may not implement "
                    "round-trip echo for probe traffic, OR fan-out requires at "
                    "least one other connected operator.",
                    file=sys.stderr,
                )
                return 2

            try:
                reply = json.loads(reply_raw)
            except json.JSONDecodeError:
                print(
                    f"<- Relay replied but the payload is not valid JSON: "
                    f"{reply_raw[:200]!r}",
                    file=sys.stderr,
                )
                return 2

            if reply.get("probe_nonce") == envelope["probe_nonce"]:
                print("<- Echo OK. Relay round-trip works for this operator.")
                return 0
            print(f"<- Relay replied with a different message: {reply}")
            print("   (May be unrelated relay traffic; not a clear pass.)")
            return 1

    except InvalidStatus as exc:
        # Auth rejection lands here on most servers.
        print(f"<- Relay rejected the handshake: {exc}", file=sys.stderr)
        if not token:
            print(
                "   (Expected: no bearer token was sent. A spec-compliant "
                "relay MUST reject. Re-run with MEMFORGE_RELAY_BEARER_TOKEN "
                "set to verify the accept path.)",
                file=sys.stderr,
            )
        return 1
    except ConnectionClosed as exc:
        print(f"<- Connection closed unexpectedly: {exc}", file=sys.stderr)
        return 1
    except (WebSocketException, OSError) as exc:
        print(f"<- Connection failed: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="Relay URL (wss:// or ws:// for loopback dev only).")
    parser.add_argument(
        "--operator-uuid",
        required=True,
        help="Operator-UUID to use in the envelope identity block.",
    )
    parser.add_argument(
        "--insecure-localhost",
        action="store_true",
        help="Allow ws:// scheme for localhost-only development.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-step timeout in seconds (default 10).",
    )
    args = parser.parse_args()

    return asyncio.run(
        _probe(
            args.url,
            args.operator_uuid,
            args.insecure_localhost,
            args.timeout,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
