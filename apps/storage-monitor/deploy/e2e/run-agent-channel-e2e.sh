#!/usr/bin/env bash
# End-to-end test of the M3a agent update channel against a disposable host.
#
# The host is a privileged docker container from deploy/e2e/Dockerfile:
# Ubuntu 20.04 with systemd 245 as PID 1, sshd and sudo. Nothing here touches a
# real storage host or needs sudo on this machine. It:
#   1. builds three bundles in a scratch git repo (A through the real docker
#      hstscan build, B an agent change, C whose hstscan miscounts) and signs
#      them with a throwaway key;
#   2. installs the legacy agent with install-agent.sh, as hosts have it today;
#   3. runs bootstrap-agent.sh from this machine over ssh, twice;
#   4. drives the gate with the central key: forced command, observe, rescan,
#      stage/activate B, a tampered bundle, C, a busy lock, rollback to A;
#   5. runs bootstrap-agent.sh --revert and checks the legacy files are back.
#
# Usage: deploy/e2e/run-agent-channel-e2e.sh   (needs docker, git, ssh-keygen)
# E2E_KEEP=1 keeps the container and work directory for debugging.
set -euo pipefail

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$E2E_DIR/../.." && pwd)"
IMAGE="storage-viz-agent-e2e:local"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-e2e.XXXXXX")"
CID=""
cleanup() {
  if [[ "${E2E_KEEP:-0}" == 1 ]]; then
    printf 'kept container %s and %s\n' "$CID" "$WORK" >&2
    return
  fi
  [[ -z "$CID" ]] || docker rm -f "$CID" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# -- 1. keys and bundles ---------------------------------------------------------
ssh-keygen -q -t ed25519 -N '' -C e2e-admin -f "$WORK/admin"
ssh-keygen -q -t ed25519 -N '' -C e2e-central -f "$WORK/central"
ssh-keygen -q -t ed25519 -N '' -C e2e-release-test-only -f "$WORK/release"
printf 'storage-viz-release namespaces="storage-viz-agent" %s\n' "$(cut -d' ' -f1,2 "$WORK/release.pub")" > "$WORK/allowed_signers"

repo="$WORK/repo"
mkdir -p "$repo/apps/storage-monitor"
for module in "$APP_ROOT"/agent/storage_viz_agent/*.py; do
  install -Dm644 "$module" "$repo/apps/storage-monitor/agent/storage_viz_agent/$(basename "$module")"
done
for rel in agent/__init__.py agent/release_cli.py \
           agent/bin/storage-viz-agent agent/RELEASE-FLOOR.json storage_fstypes.py scanner/hstscan.c deploy/build-agent-release.py; do
  mkdir -p "$repo/apps/storage-monitor/$(dirname "$rel")"
  cp -p "$APP_ROOT/$rel" "$repo/apps/storage-monitor/$rel"
done
g() { git -C "$repo" -c user.name=e2e -c user.email=e2e@example.invalid -c commit.gpgsign=false "$@"; }
g init -q
build() {  # build NAME [extra builder args]
  local name="$1"; shift
  g add -A && g commit -qm "$name"
  python3 "$repo/apps/storage-monitor/deploy/build-agent-release.py" --repo "$repo" \
    --sha "$(g rev-parse HEAD)" --output-dir "$WORK/$name" "$@" >/dev/null
  AGENT_SIGNING_KEY="$(cat "$WORK/release")" bash "$APP_ROOT/deploy/sign-agent-release.sh" "$WORK/$name" "$WORK/allowed_signers" >/dev/null
}
sha_of() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_sha"])' "$WORK/$1/storage-viz-agent.json"; }
build A
mkdir "$WORK/A-x" && tar -xzf "$WORK/A/storage-viz-agent.tar.gz" -C "$WORK/A-x"
echo '# release B' >> "$repo/apps/storage-monitor/agent/release_cli.py"
build B --hstscan-binary "$WORK/A-x/bin/hstscan"
printf '#!/bin/sh\nwhile [ "$1" != --out ]; do shift; done\nprintf %s > "$2"\n' \
  "'{\"mounts\":[{\"scanned_files\":1,\"scanned_dirs\":1,\"scanned_bytes\":1,\"errors\":0}]}'" > "$WORK/liar"
echo '# release C' >> "$repo/apps/storage-monitor/agent/release_cli.py"
build C --hstscan-binary "$WORK/liar"
SHA_A="$(sha_of A)"; SHA_B="$(sha_of B)"; SHA_C="$(sha_of C)"
pass "built and signed bundles A=${SHA_A:0:8} (docker hstscan) B=${SHA_B:0:8} C=${SHA_C:0:8} (miscounting hstscan)"

# The operator's checkout, with the test release key pinned instead of the real one.
app="$WORK/app"
mkdir -p "$app"
tar -C "$APP_ROOT" --exclude=__pycache__ --exclude=.pytest_cache -cf - agent deploy scanner storage_fstypes.py | tar -C "$app" -xf -
cp "$WORK/allowed_signers" "$app/agent/bootstrap/allowed_signers"

# -- 2. a host with today's hand-installed agent ---------------------------------
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -q -t "$IMAGE" "$E2E_DIR" >/dev/null
CID="$(docker run -d --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock -p 127.0.0.1::22 "$IMAGE")"
for _ in $(seq 1 60); do
  state="$(docker exec "$CID" systemctl is-system-running 2>/dev/null || true)"
  [[ "$state" == running || "$state" == degraded ]] && break
  sleep 1
done
PORT="$(docker port "$CID" 22/tcp | head -1 | sed 's/.*://')"
docker cp "$WORK/admin.pub" "$CID:/home/admin/.ssh/authorized_keys"
docker cp "$WORK/central.pub" "$CID:/home/monitoring/.ssh/authorized_keys"
docker exec "$CID" sh -c 'chown admin:admin /home/admin/.ssh/authorized_keys; chown monitoring:monitoring /home/monitoring/.ssh/authorized_keys'
docker cp "$app" "$CID:/root/legacy-src"
docker exec "$CID" bash /root/legacy-src/deploy/install-agent.sh --server-id e2e-host >/dev/null
docker exec "$CID" systemctl is-enabled storage-viz-scan.timer >/dev/null || fail "legacy timer not enabled"
LEGACY_SNAPSHOT='cd / && stat -c "%n %a %U:%G" home/monitoring && for p in home/monitoring/.ssh etc/sudoers.d/storage-viz-monitoring etc/sudoers.d/storage-viz etc/systemd/system/storage-viz-scan.service etc/systemd/system/storage-viz-scheduled-scan.service etc/systemd/system/storage-viz-scan.timer etc/systemd/system/storage-viz-agent-update.service opt/storage-viz usr/local/lib/storage-viz etc/storage-viz; do if [ -e "$p" ]; then find "$p" -printf "%p %m %u:%g %y\n" | sort; find "$p" -type f -exec sha256sum {} + | sort; else echo "absent $p"; fi; done; systemctl is-enabled storage-viz-scan.timer'
before="$(docker exec "$CID" sh -c "$LEGACY_SNAPSHOT")"
pass "legacy agent installed by install-agent.sh on Ubuntu 20.04 ($(docker exec "$CID" systemctl --version | head -1))"

SSH=(-oBatchMode=yes -oStrictHostKeyChecking=no -oUserKnownHostsFile="$WORK/known_hosts" -oIdentitiesOnly=yes -oPort="$PORT" -oLogLevel=ERROR)
bootstrap() {
  "$app/deploy/bootstrap-agent.sh" --admin admin --bundle-dir "$WORK/A" --central-speaks-gate \
    --ssh-opt "-oIdentityFile=$WORK/admin" $(printf -- '--ssh-opt %s ' "${SSH[@]}") "$@" 127.0.0.1
}
gate() { ssh "${SSH[@]}" -oIdentityFile="$WORK/central" monitoring@127.0.0.1 "$@"; }
observe() { gate observe | tail -n +2 | python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {'d': d}))" "$1"; }
wait_update() {  # wait_update SHA: the updater finished an attempt for SHA
  for _ in $(seq 1 120); do
    if [[ "$(observe "(d['update'] or {}).get('last_attempt', {}).get('sha')")" == "$1" && \
          "$(observe "d['units']['storage-viz-agent-update.service']['ActiveState']")" != activ* ]]; then
      observe "d['update']['last_attempt']['outcome'] + ' ' + str(d['update']['last_attempt']['code'])"
      return
    fi
    sleep 1
  done
  fail "updater did not finish for $1"
}
stage() {  # stage DIR SHA
  local size; size="$(stat -c %s "$1/storage-viz-agent.tar.gz")"
  cat "$1/storage-viz-agent.tar.gz" "$1/storage-viz-agent.tar.gz.sig" | gate "stage-update $2 $size" | tail -n +2
}

# -- 3. bootstrap ---------------------------------------------------------------
set +e
"$app/deploy/bootstrap-agent.sh" --admin admin --bundle-dir "$WORK/A" --ssh-opt "-oIdentityFile=$WORK/admin" \
  $(printf -- '--ssh-opt %s ' "${SSH[@]}") 127.0.0.1 > "$WORK/guard.log" 2>&1
rc_guard=$?
set -e
[[ $rc_guard -eq 2 ]] || fail "bootstrap ran without --central-speaks-gate (rc=$rc_guard)"
[[ "$(docker exec "$CID" sh -c "$LEGACY_SNAPSHOT")" == "$before" ]] || fail "the refused bootstrap changed the host"
bootstrap > "$WORK/bootstrap1.log" 2>&1 || { cat "$WORK/bootstrap1.log"; fail "bootstrap failed"; }
grep -q '"schema": "storage-viz.observe/1"' "$WORK/bootstrap1.log" || fail "bootstrap did not print observe"
bootstrap > "$WORK/bootstrap2.log" 2>&1 || { cat "$WORK/bootstrap2.log"; fail "second bootstrap failed"; }
grep -q 'keeping the existing pre-bootstrap backup' "$WORK/bootstrap2.log" || fail "rerun replaced the backup"
grep -q '^restrict,command="/usr/local/lib/storage-viz/gate" ssh-ed25519 ' <(docker exec "$CID" cat /home/monitoring/.ssh/authorized_keys) || fail "forced command missing"
[[ "$(docker exec "$CID" stat -c '%U %a' /home/monitoring /home/monitoring/.ssh /home/monitoring/.ssh/authorized_keys | sort -u)" == $'root 644\nroot 755' ]] \
  || fail "monitoring's key path is not root-owned"
pass "bootstrap-agent.sh --central-speaks-gate ran over ssh as admin with sudo (preflight self-test, visudo -cf, systemd-analyze verify), rerun converged, key path root-owned and accepted by sshd StrictModes"

[[ "$(observe "d['agent']['release_sha']")" == "$SHA_A" ]] || fail "release A not current"
[[ "$(observe "d['agent']['health']")" == ok ]] || fail "agent health is not ok"
[[ "$(observe "d['update']['active']")" == "$SHA_A" ]] || fail "state.json does not name A"
docker exec "$CID" systemctl is-enabled storage-viz-scan.timer >/dev/null || fail "timer not enabled"
pass "release A active through the updater unit (self-test under systemd-run), agent health ok, timer enabled"

# -- 4. the gate ------------------------------------------------------------------
for cmd in "id" "observe; id" "sh -c id" "get-status x" "scp -t /tmp"; do
  set +e; out="$(gate "$cmd" 2>&1)"; rc=$?; set -e
  [[ $rc -eq 2 && "$out" == "storage-viz-gate/1 err USAGE" ]] || fail "gate accepted '$cmd' (rc=$rc: $out)"
done
set +e; ssh "${SSH[@]}" -oIdentityFile="$WORK/central" -s monitoring@127.0.0.1 sftp </dev/null >/dev/null 2>&1; rc_sftp=$?
ssh "${SSH[@]}" -oIdentityFile="$WORK/central" monitoring@127.0.0.1 </dev/null >/dev/null 2>&1; rc_shell=$?
ssh "${SSH[@]}" -oIdentityFile="$WORK/central" -oExitOnForwardFailure=yes -N -L 127.0.0.1:0:127.0.0.1:22 monitoring@127.0.0.1 </dev/null >/dev/null 2>&1 & fwd=$!
sleep 3; kill -0 "$fwd" 2>/dev/null; fwd_alive=$?; kill "$fwd" 2>/dev/null; wait "$fwd" 2>/dev/null; set -e
[[ $rc_sftp -ne 0 && $rc_shell -eq 2 && $fwd_alive -ne 0 ]] || fail "sftp=$rc_sftp shell=$rc_shell forward_alive=$((1 - fwd_alive))"
pass "forced command: non-verbs, shell text, sftp, interactive login and port forwarding are refused"

request="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
[[ "$(gate "rescan $request" | tail -n +2)" == received ]] || fail "rescan not received"
[[ "$(gate "rescan $request" | tail -n +2)" == already_received ]] || fail "duplicate rescan not recognised"
for _ in $(seq 1 120); do
  inv="$(observe "d['units']['storage-viz-scan.service']['InvocationID']")"
  act="$(observe "d['units']['storage-viz-scan.service']['ActiveState']")"
  [[ "$inv" != None && "$act" != activ* ]] && break
  sleep 1
done
[[ "$inv" != None ]] || fail "rescan did not start storage-viz-scan.service"
[[ "$(gate get-manifest | tail -n +2 | python3 -c 'import json,sys; print(json.load(sys.stdin)["schema"])')" == storage-viz.manifest/2 ]] || fail "rescan did not publish v2 manifest"
pass "rescan started the release and published a v2 manifest"

[[ "$(stage "$WORK/B" "$SHA_B")" == staged ]] || fail "stage B"
gate "activate-update $SHA_B" >/dev/null
[[ "$(wait_update "$SHA_B")" == "activated None" ]] || fail "B not activated"
[[ "$(observe "d['agent']['release_sha'] + ' ' + d['update']['previous']")" == "$SHA_B $SHA_A" ]] || fail "B/A not current/previous"
pass "stage-update + activate-update moved the host to B; A kept as previous"

cp -r "$WORK/C" "$WORK/T"
python3 - "$WORK/T/storage-viz-agent.tar.gz" <<'PY'
import sys
data = bytearray(open(sys.argv[1], "rb").read()); data[len(data) // 2] ^= 1
open(sys.argv[1], "wb").write(data)
PY
stage "$WORK/T" "$SHA_C" >/dev/null
gate "activate-update $SHA_C" >/dev/null
[[ "$(wait_update "$SHA_C")" == "failed SIGNATURE" ]] || fail "tampered bundle not refused by signature"
[[ "$(observe "d['agent']['release_sha']")" == "$SHA_B" ]] || fail "tampered bundle changed current"
pass "tampered bundle refused (SIGNATURE), B still current"

stage "$WORK/C" "$SHA_C" >/dev/null
gate "activate-update $SHA_C" >/dev/null
[[ "$(wait_update "$SHA_C")" == "failed SELFTEST_FAILED" ]] || fail "self-test failure not detected"
[[ "$(observe "d['agent']['release_sha'] + ' ' + str(d['update']['bad'] == [] and d['update']['selftest_backoff']['$SHA_C']['failures'] == 1)")" == "$SHA_B True" ]] \
  || fail "C activated, or not backed off"
gate "activate-update $SHA_C" >/dev/null
[[ "$(wait_update "$SHA_C")" == "refused SELFTEST_BACKOFF" ]] || fail "C retried inside its backoff"
pass "signed bundle whose hstscan miscounts failed the sandboxed self-test: not activated, backed off 6 h (not marked bad)"

docker exec -d "$CID" flock /run/storage-viz/scan.lock sleep 15
sleep 1
gate "activate-update $SHA_B" >/dev/null
[[ "$(wait_update "$SHA_B")" == "busy BUSY" ]] || fail "busy lock not deferred"
docker exec "$CID" test -e /var/lib/storage-viz/update/incoming/activate || fail "busy request was dropped"
[[ "$(docker exec "$CID" systemctl show -p ActiveState --value storage-viz-agent-update.service)" == inactive ]] || fail "exit 75 left the unit failed"
pass "update during a held scan lock deferred with BUSY (exit 75, unit not failed), request kept"
sleep 15

gate "rollback-update $SHA_A" >/dev/null
[[ "$(wait_update "$SHA_A")" == "rolled_back None" ]] || fail "rollback failed"
[[ "$(observe "d['agent']['release_sha'] + ' ' + str([b['sha'] for b in d['update']['bad']] == ['$SHA_B'])")" == "$SHA_A True" ]] \
  || fail "rollback did not restore A or mark B bad"
gate "activate-update $SHA_B" >/dev/null
[[ "$(wait_update "$SHA_B")" == "refused BAD_RELEASE" ]] || fail "rolled-back release activated again"
pass "rollback-update restored A, B marked bad and refused afterwards"

# -- 5. revert ---------------------------------------------------------------------
bootstrap --revert > "$WORK/revert.log" 2>&1 || { cat "$WORK/revert.log"; fail "revert failed"; }
after="$(docker exec "$CID" sh -c "$LEGACY_SNAPSHOT")"
if [[ "$before" != "$after" ]]; then
  diff <(echo "$before") <(echo "$after") >&2 || true
  fail "revert did not restore the legacy files"
fi
gate observe >/dev/null 2>&1 && fail "monitoring still has the forced command after revert" || true
pass "bootstrap-agent.sh --revert restored authorized_keys, sudoers, units, /opt/storage-viz and the enabled timer byte for byte"
echo "e2e: all checks passed"
