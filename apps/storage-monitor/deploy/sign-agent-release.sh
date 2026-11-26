#!/usr/bin/env bash
# Sign a built agent bundle with the CI release key (target-design §4.5).
#
# Usage: AGENT_SIGNING_KEY="$(cat key)" sign-agent-release.sh OUTPUT_DIR [ALLOWED_SIGNERS]
#
# OUTPUT_DIR holds storage-viz-agent.tar.gz from build-agent-release.py. The
# private key comes only from the AGENT_SIGNING_KEY environment variable (a
# GitHub environment secret restricted to main). It touches disk only in a 0600
# temp file that is removed on exit. The signature is checked against the
# allowed_signers file that hosts pin (default: agent/bootstrap/allowed_signers),
# so a key that hosts would refuse never produces a published signature.
set -euo pipefail

NAMESPACE="storage-viz-agent"
IDENTITY="storage-viz-release"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="${1:?usage: sign-agent-release.sh OUTPUT_DIR [ALLOWED_SIGNERS]}"
ALLOWED_SIGNERS="${2:-$SCRIPT_DIR/../agent/bootstrap/allowed_signers}"
BUNDLE="$OUTPUT_DIR/storage-viz-agent.tar.gz"

[[ -n "${AGENT_SIGNING_KEY:-}" ]] || { echo "ERROR: AGENT_SIGNING_KEY is empty" >&2; exit 2; }
[[ -f "$BUNDLE" && ! -L "$BUNDLE" ]] || { echo "ERROR: missing bundle: $BUNDLE" >&2; exit 2; }

keydir="$(mktemp -d)"
trap 'rm -rf "$keydir"' EXIT
( umask 077; printf '%s\n' "$AGENT_SIGNING_KEY" > "$keydir/key" )
rm -f "$BUNDLE.sig"
ssh-keygen -q -Y sign -f "$keydir/key" -n "$NAMESPACE" "$BUNDLE"
if ! ssh-keygen -Y verify -f "$ALLOWED_SIGNERS" -I "$IDENTITY" -n "$NAMESPACE" -s "$BUNDLE.sig" < "$BUNDLE" >/dev/null; then
  rm -f "$BUNDLE.sig"
  echo "ERROR: the signing key is not in $ALLOWED_SIGNERS; hosts would refuse this bundle" >&2
  exit 1
fi
echo "signed $BUNDLE"
