#!/usr/bin/env bash
# Rehearsal of the one-time redesign deploy, from the deploy tools installed on the
# central host today to the new release, in disposable docker containers only.
#
# It rebuilds today's central state and then runs the candidate runbooks against it:
#   1. sources: a local bare "GitHub" repository holding today's main, the release
#      that is live, and a merge commit of PR #16 built from this checkout;
#   2. two storage hosts (deploy/e2e/Dockerfile, Ubuntu 20.04, systemd 245) with
#      today's v1 agent installed by install-agent.sh from the live commit and one
#      v1 scan of a /data volume each by the real v1 runner and hstscan (a mountinfo
#      shim lets the v1 policy accept the docker volume, see rehearsal_v1_scan_shim.py);
#   3. a central host (deploy/e2e/central.Dockerfile, Ubuntu 22.04 like the real one)
#      built the way the real one was: install.sh from the live commit, the deploy
#      tools from their installed commit by that commit's own installer, the previous
#      release made the first managed one by its own builder and the installed activator (a
#      stand-in for the one-time --bootstrap-cutover), the installed puller activating
#      the live release from the fake GitHub, the timer enabled, and the old store written by the live
#      release itself (a v1 rescan seeds its job and duration history);
#   4. scenario naive: merge first, so the installed puller sees the new SHA;
#   5. scenario proposed, on a fresh central: stop the timer, merge, rerun the
#      installer from the new main, start the puller once, start the timer, then
#      check the import (C8), the v2 fleet, the viewer and a v1 rescan;
#   6. rollback with the activator's --rollback-state, the old store (R1), both
#      health checkers, and roll-forward.
#
# Nothing here touches the host it runs on beyond docker objects named $RUN-*,
# which it removes. The fake GitHub serves HTTPS as api.github.com inside the
# central container only, and the builder's git config maps the clone URL to the
# local bare repository, so every installed tool runs unmodified.
#
# Usage: deploy/e2e/run-dashboard-upgrade-rehearsal.sh   (needs docker and git)
# E2E_KEEP=1 keeps the containers and work directory for debugging.
set -euo pipefail
shopt -s inherit_errexit  # a failure inside $(...) fails the rehearsal

E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$E2E_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$APP_ROOT/../.." && pwd)"
# The commit every tool in /usr/local/libexec matches by sha256 (see the runbook in docs/operations.md).
TOOLS_SHA="${REHEARSAL_TOOLS_SHA:-c09a7c2}"
# The live release (/opt/storage-viz-dashboard -> releases/<sha>, RELEASE-MANIFEST source_sha) and the one before it.
LIVE_SHA="${REHEARSAL_LIVE_SHA:-06cdd5f773fcee96bde41997a4ab83ea684ca59c}"
PREV_SHA="${REHEARSAL_PREV_SHA:-b219a46b7cfca1de546edc1b80a0a323fa993b67}"
HOST_IMAGE="storage-viz-agent-e2e:local"
CENTRAL_IMAGE="storage-viz-central-rehearsal:local"
HOSTS=(host-a host-b)
RUN="svr-rehearsal-$$"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-rehearsal.XXXXXX")"
VOLUMES=()
NETWORK=""

cleanup() {
  if [[ "${E2E_KEEP:-0}" == 1 ]]; then
    printf 'kept containers %s, network %s and %s\n' "$(tr '\n' ' ' < "$WORK/containers" 2>/dev/null)" "$NETWORK" "$WORK" >&2
    return
  fi
  while read -r cid; do docker rm -f "$cid" >/dev/null 2>&1 || true; done < "$WORK/containers" 2>/dev/null || true
  for vol in "${VOLUMES[@]}"; do docker volume rm "$vol" >/dev/null 2>&1 || true; done
  [[ -z "$NETWORK" ]] || docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
# Containers start inside $(...) subshells too, so their ids go to a file the cleanup reads.
track() { echo "$1" >> "$WORK/containers"; }

pass() { printf 'PASS: %s\n' "$*"; }
note() { printf 'NOTE: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
in_c() { local cid="$1"; shift; docker exec "$cid" bash -c "set -euo pipefail; $*"; }
json_get() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {}, {"d": d}))' "$1"; }

wait_systemd() {
  local cid="$1" state=""
  for _ in $(seq 1 90); do
    state="$(docker exec "$cid" systemctl is-system-running 2>/dev/null || true)"
    [[ "$state" == running || "$state" == degraded ]] && return 0
    sleep 1
  done
  fail "systemd in $cid did not come up ($state)"
}

# -- 1. sources ------------------------------------------------------------------------
git -C "$REPO_ROOT" rev-parse -q --verify "$TOOLS_SHA^{commit}" >/dev/null || fail "tools commit $TOOLS_SHA missing"
TOOLS_SHA="$(git -C "$REPO_ROOT" rev-parse "$TOOLS_SHA^{commit}")"
HEAD_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
BARE="$WORK/github/GPU_monitor.git"
git init -q --bare "$BARE"
# --update-shallow: the operator's checkout may be shallow.
git --git-dir "$BARE" fetch -q --update-shallow "$REPO_ROOT" "$LIVE_SHA:refs/heads/main" "$PREV_SHA:refs/tags/rehearsal-prev" \
  "$TOOLS_SHA:refs/tags/rehearsal-tools" "$HEAD_SHA:refs/heads/redesign/storage-monitor"
# PR #16 merged on GitHub: a merge commit of the live main and this checkout's tree, uncommitted edits included.
export GIT_AUTHOR_NAME=rehearsal GIT_AUTHOR_EMAIL=rehearsal@example.invalid GIT_COMMITTER_NAME=rehearsal GIT_COMMITTER_EMAIL=rehearsal@example.invalid
bare_wt() { GIT_DIR="$BARE" GIT_WORK_TREE="$REPO_ROOT" GIT_INDEX_FILE="$WORK/index" git "$@"; }
bare_wt read-tree "$HEAD_SHA"
bare_wt add -A
MERGE_TREE="$(bare_wt write-tree)"
MERGE_SHA="$(git --git-dir "$BARE" commit-tree "$MERGE_TREE" -p "$LIVE_SHA" -p "$HEAD_SHA" -m 'Merge pull request #16 from IRCVLab/redesign/storage-monitor (rehearsal)')"
git --git-dir "$BARE" update-ref refs/heads/rehearsal-merge "$MERGE_SHA"
git --git-dir "$BARE" config uploadpack.allowFilter true
git --git-dir "$BARE" config uploadpack.allowAnySHA1InWant true
for name in tools:"$TOOLS_SHA" live:"$LIVE_SHA" new:"$MERGE_SHA"; do
  mkdir -p "$WORK/src-${name%%:*}"
  git --git-dir "$BARE" archive "${name#*:}" | tar -x -C "$WORK/src-${name%%:*}"
done
dirty="$(git --git-dir "$BARE" diff --stat "$HEAD_SHA" "$MERGE_SHA" -- | tail -1)"
pass "sources: tools ${TOOLS_SHA:0:7}, live ${LIVE_SHA:0:7}, previous ${PREV_SHA:0:7}, merge ${MERGE_SHA:0:7} of HEAD ${HEAD_SHA:0:7}${dirty:+ plus uncommitted: $dirty}"

# -- 2. storage hosts with today's v1 agent ----------------------------------------------
docker image inspect "$HOST_IMAGE" >/dev/null 2>&1 || docker build -q -t "$HOST_IMAGE" "$E2E_DIR" >/dev/null
docker image inspect "$CENTRAL_IMAGE" >/dev/null 2>&1 || docker build -q -t "$CENTRAL_IMAGE" -f "$E2E_DIR/central.Dockerfile" "$E2E_DIR" >/dev/null
NETWORK="$(docker network create "$RUN" >/dev/null && echo "$RUN")"
ssh-keygen -q -t ed25519 -N '' -C rehearsal-central -f "$WORK/central"
declare -A HOST_CID
for host in "${HOSTS[@]}"; do
  docker volume create "$RUN-$host" >/dev/null && VOLUMES+=("$RUN-$host")
  cid="$(docker run -d --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock --network "$NETWORK" \
    --network-alias "$host" --hostname "$host" -v "$RUN-$host:/data" "$HOST_IMAGE")"
  track "$cid"; HOST_CID[$host]="$cid"
done
for host in "${HOSTS[@]}"; do
  cid="${HOST_CID[$host]}"
  wait_systemd "$cid"
  docker cp "$WORK/central.pub" "$cid:/home/monitoring/.ssh/authorized_keys"
  docker cp "$WORK/src-live/apps/storage-monitor" "$cid:/root/legacy-src"
  docker exec "$cid" mkdir -p /usr/local/lib/storage-viz-rehearsal /etc/systemd/system/storage-viz-scan.service.d
  docker cp "$E2E_DIR/rehearsal_v1_scan_shim.py" "$cid:/usr/local/lib/storage-viz-rehearsal/scan_shim.py"
  # Log every SFTP open, so the rehearsal can count what central downloads (C8).
  in_c "$cid" "chown monitoring:monitoring /home/monitoring/.ssh/authorized_keys
    sed -i 's#^Subsystem.*sftp.*#Subsystem sftp /usr/lib/openssh/sftp-server -l INFO#' /etc/ssh/sshd_config && systemctl restart ssh
    mkdir -p /data/projects/alpha /data/projects/beta
    head -c \$((3 * 1024 * 1024)) /dev/urandom > /data/projects/alpha/weights.bin
    head -c \$((1024 * 1024)) /dev/urandom > /data/projects/beta/log.bin
    chown -R monitoring:monitoring /data/projects/alpha
    bash /root/legacy-src/deploy/install-agent.sh --server-id $host >/dev/null
    systemctl stop storage-viz-scan.timer
    printf '[Service]\nExecStart=\nExecStart=/usr/bin/python3 /usr/local/lib/storage-viz-rehearsal/scan_shim.py --config /etc/storage-viz/scanner.yaml\n' \
      > /etc/systemd/system/storage-viz-scan.service.d/rehearsal.conf
    systemctl daemon-reload
    systemctl start storage-viz-scan.service
    test -s /var/lib/storage-viz/scan-status.json"
done
pass "hosts ${HOSTS[*]}: v1 agent from install-agent.sh @ ${LIVE_SHA:0:7}, one v1 scan of /data each by the real v1 runner (rehearsal_v1_scan_shim.py), timers stopped"

# -- 3. a central host in today's state --------------------------------------------------
puller() {  # puller CID: one run of the installed puller unit; prints its last line (the result or ERROR: ...)
  local cid="$1" since rc=0
  since="$(in_c "$cid" 'date +%s.%N')"
  in_c "$cid" 'systemctl start storage-monitor-release-puller.service' >/dev/null 2>&1 || rc=$?
  in_c "$cid" "journalctl -q -u storage-monitor-release-puller.service --since @$since -o cat | grep -v -e '^Start' -e 'pam_unix' -e 'Consumed' -e '^Finish' -e 'Deactivated' -e 'Main process exited' -e 'Failed with result' -e '^Failed to start' | tail -n 1" || true
  return $rc
}
set_main() { in_c "$1" "git --git-dir /srv/fake-github/GPU_monitor.git update-ref refs/heads/main $2"; }
health() {  # health CID CHECKER: exit status of a health checker against the running release
  in_c "$1" "$2" >/dev/null 2>&1
}
store_digest() {  # store_digest CID: digest of the old store STATE_DIR/<sid>/ and the duration history
  in_c "$1" "cd /var/lib/storage-viz-dashboard/state && find ${HOSTS[*]} scan-duration-history -type f 2>/dev/null | sort | xargs sha256sum | sha256sum | cut -c1-16"
}
scan_starts() {  # scan_starts HOST: how many times storage-viz-scan.service has started on HOST
  in_c "${HOST_CID[$1]}" "journalctl -q -u storage-viz-scan.service -o cat | grep -c '^Starting storage-viz local filesystem scan' || true"
}
sftp_snapshot_opens() {  # sftp_snapshot_opens SINCE_EPOCH: snapshot files central opened over SFTP on all hosts
  local total=0 n
  for host in "${HOSTS[@]}"; do
    n="$(in_c "${HOST_CID[$host]}" "journalctl -q --since @$1 -o cat | grep -c 'open \"/var/lib/storage-viz/snapshots/' || true")"
    total=$((total + n))
  done
  echo "$total"
}

boot_central() {  # boot_central NAME: prints the container id of a central in today's live state
  local name="$1" cid ip
  cid="$(docker run -d --privileged --cgroupns=private --tmpfs /run --tmpfs /run/lock --network "$NETWORK" \
    --network-alias "$name" --hostname "$name" --add-host api.github.com:127.0.0.1 "$CENTRAL_IMAGE")"
  track "$cid"
  wait_systemd "$cid"
  ip="$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NETWORK\").IPAddress}}" "$cid")"
  docker cp "$E2E_DIR" "$cid:/rehearsal"
  docker cp "$BARE" "$cid:/srv/fake-github-repo"
  docker cp "$WORK/src-live" "$cid:/root/live-main"
  docker cp "$WORK/src-tools" "$cid:/root/tools-main"
  docker cp "$WORK/central" "$cid:/root/central_ed25519"
  in_c "$cid" "
    mkdir -p /srv/fake-github && mv /srv/fake-github-repo /srv/fake-github/GPU_monitor.git && chown -R root:root /srv/fake-github && chmod -R a+rX /srv/fake-github
    cd /root && openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=rehearsal-ca -keyout ca.key -out ca.crt 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj /CN=api.github.com -keyout gh.key -out gh.csr 2>/dev/null
    printf 'subjectAltName=DNS:api.github.com\n' > gh.ext
    openssl x509 -req -in gh.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 -extfile gh.ext -out gh.crt 2>/dev/null
    cp ca.crt /usr/local/share/ca-certificates/rehearsal-ca.crt && update-ca-certificates >/dev/null 2>&1
    systemd-run -q --unit fake-github python3 /rehearsal/rehearsal_github.py --repo /srv/fake-github/GPU_monitor.git --cert /root/gh.crt --key /root/gh.key --log /root/fake-github.log
    mkdir -p /etc/storage-viz/keys
    install -m 0640 /root/central_ed25519 /etc/storage-viz/keys/central_ed25519
    ssh-keyscan -t ed25519 ${HOSTS[*]} > /etc/storage-viz/known_hosts 2>/dev/null
    chmod 0644 /etc/storage-viz/known_hosts
    python3 - <<'PY'
import json
servers = [{\"id\": h, \"display_name\": h.upper(), \"order\": 10 * (i + 1), \"host\": h, \"port\": 22, \"enabled\": True,
            \"username\": \"monitoring\", \"identity_file\": \"/etc/storage-viz/keys/central_ed25519\",
            \"known_hosts_file\": \"/etc/storage-viz/known_hosts\",
            \"scanner\": {\"server_id\": h, \"scanner_path\": \"/opt/storage-viz/scanner/hstscan\", \"data_dir\": \"/var/lib/storage-viz\",
                        \"run_dir\": \"/run/storage-viz\", \"threads\": 4, \"prune_home_mb\": 50, \"prune_data_mb\": 100, \"top\": 200, \"stale_days\": 180}}
           for i, h in enumerate('${HOSTS[*]}'.split())]
json.dump({\"servers\": servers}, open('/etc/storage-viz/servers.json', 'w'), indent=2)
PY
    cat > /etc/storage-viz/dashboard.env <<ENV
STORAGE_VIZ_BIND=127.0.0.1
STORAGE_VIZ_PORT=8088
STORAGE_VIZ_INVENTORY=/etc/storage-viz/servers.json
STORAGE_VIZ_TRUSTED_PROXY=1
STORAGE_VIZ_ALLOWED_ORIGINS=http://storage.internal:505
STORAGE_VIZ_OPERATOR_ALLOWLIST=fixed-proxy-operator
STORAGE_VIZ_SESSION_COOKIE_SECURE=0
ENV
    cat > /etc/storage-viz/proxy.env <<ENV
STORAGE_VIZ_PROXY_BIND=$ip
STORAGE_VIZ_PROXY_PORT=8088
STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1
STORAGE_VIZ_PROXY_UPSTREAM_PORT=8088
STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator
STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://storage.internal:505
ENV
    bash /root/live-main/apps/storage-monitor/install.sh >/dev/null
    chown root:storage-viz /etc/storage-viz /etc/storage-viz/keys /etc/storage-viz/keys/central_ed25519 /etc/storage-viz/servers.json /etc/storage-viz/dashboard.env /etc/storage-viz/proxy.env
    chmod 0640 /etc/storage-viz/proxy.env
    bash /root/tools-main/apps/storage-monitor/deploy/server/install-dashboard-deployer.sh >/root/install-tools.log
    cat > /var/lib/storage-viz-dashboard/builder/.gitconfig <<GIT
[url \"file:///srv/fake-github/GPU_monitor.git\"]
	insteadOf = https://github.com/IRCVLab/GPU_monitor.git
[safe]
	directory = /srv/fake-github/GPU_monitor.git
GIT
    chown storage-viz-builder:storage-viz-builder /var/lib/storage-viz-dashboard/builder/.gitconfig
    for f in /usr/local/libexec/*.py; do sha256sum -c --quiet \"\$f.sha256\"; done" >&2
  local tools_ok
  tools_ok="$(in_c "$cid" "cd /usr/local/libexec && for f in $TOOL_NAMES; do sha256sum \$f | cut -d' ' -f1; done")"
  [[ "$tools_ok" == "$EXPECTED_TOOL_DIGESTS" ]] || fail "$name: installed tools differ from tools commit ${TOOLS_SHA:0:7}"
  # Stand-in for the one-time --bootstrap-cutover (which also retired the hand-run legacy proxy):
  # the release's own builder (as the puller runs it) and the installed activator make the previous release the first managed one, and
  # the proxy starts once activation-state.json exists, as the launcher requires.
  local art="storage-monitor-dashboard-$PREV_SHA"
  in_c "$cid" "git clone -q /srv/fake-github/GPU_monitor.git /root/prev-checkout 2>/dev/null && cd /root/prev-checkout && git checkout -q --detach $PREV_SHA
    mkdir -p /root/prev-out && python3 apps/storage-monitor/deploy/build-dashboard-release.py --sha $PREV_SHA --output-dir /root/prev-out >/dev/null
    digest=\$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"sha256\"])' /root/prev-out/$art.sha256.json)
    /usr/local/libexec/storage-dashboard-activate.py --sha $PREV_SHA --expected-digest \$digest --artifact-stdin --metadata /root/prev-out/$art.sha256.json \
      --restart-argv /usr/bin/systemctl restart storage-viz-dashboard.service < /root/prev-out/$art.tar.gz >/dev/null
    systemctl start storage-viz-proxy.service
    /usr/local/libexec/storage-dashboard-health-check.py" || fail "$name: bootstrap of ${PREV_SHA:0:7} failed"
  set_main "$cid" "$LIVE_SHA"
  [[ "$(puller "$cid")" == *activated* ]] || fail "$name: the installed puller did not activate ${LIVE_SHA:0:7}"
  in_c "$cid" 'systemctl enable --now storage-monitor-release-puller.timer' >/dev/null 2>&1
  [[ "$(in_c "$cid" 'readlink /opt/storage-viz-dashboard')" == "/srv/storage-viz-dashboard/releases/$LIVE_SHA/storage-monitor" ]] || fail "$name: live symlink"
  health "$cid" /usr/local/libexec/storage-dashboard-health-check.py || fail "$name: installed health check fails on the live release"
  # The old store, written by the live release's own collector.
  for _ in $(seq 1 120); do
    in_c "$cid" "for h in ${HOSTS[*]}; do test -f /var/lib/storage-viz-dashboard/state/\$h/current.json || exit 1; done" 2>/dev/null && break
    sleep 2
  done
  in_c "$cid" "for h in ${HOSTS[*]}; do test -f /var/lib/storage-viz-dashboard/state/\$h/current.json; done" || fail "$name: the live release wrote no old store"
  # Seed a job and duration history through the live release's own rescan. The POST returns
  # before the dashboard starts the host's scan, so wait until that scan has run.
  local seeded started
  started="$(scan_starts "${HOSTS[0]}")"
  seeded="$(in_c "$cid" "python3 /rehearsal/rehearsal_probe.py rescan-v1 --server ${HOSTS[0]}")"
  [[ "$(json_get 'd["post_status"]' <<<"$seeded")" == 202 ]] || fail "$name: v1 rescan not accepted: $seeded"
  for _ in $(seq 1 90); do
    [[ "$(scan_starts "${HOSTS[0]}")" -gt "$started" ]] \
      && ! docker exec "${HOST_CID[${HOSTS[0]}]}" systemctl -q is-active storage-viz-scan.service && break
    sleep 2
  done
  [[ "$(scan_starts "${HOSTS[0]}")" -gt "$started" ]] || fail "$name: the v1 rescan did not start the scan on ${HOSTS[0]}"
  # The old store is settled once it holds each host's latest publication (it names snapshots by
  # the digest the agent published). Host timers are stopped, so nothing changes it after that.
  local host published settled
  for _ in $(seq 1 90); do
    settled=1
    for host in "${HOSTS[@]}"; do
      published="$(in_c "${HOST_CID[$host]}" "python3 -c 'import json; print(json.load(open(\"/var/lib/storage-viz/scan-status.json\"))[\"sha256\"])'")"
      in_c "$cid" "grep -q 'snapshot-v2-$published.json' /var/lib/storage-viz-dashboard/state/$host/current.json" 2>/dev/null || settled=0
    done
    [[ $settled == 1 ]] && break
    sleep 2
  done
  [[ $settled == 1 ]] || fail "$name: the old store did not catch up with the hosts' latest snapshots"
  echo "$cid"
}

# The installed name of each tool and its source in the tools commit, in the installer's order.
TOOLS=(storage_dashboard_release.py:apps/storage-monitor/deploy/server/storage_dashboard_release.py
       storage-dashboard-build-release.py:apps/storage-monitor/deploy/build-dashboard-release.py
       storage-dashboard-activate.py:apps/storage-monitor/deploy/server/activate-dashboard-release.py
       storage-dashboard-health-check.py:apps/storage-monitor/deploy/server/health-check-dashboard.py
       storage-viz-proxy-launcher.py:apps/storage-monitor/deploy/server/storage-viz-proxy-launcher.py
       storage-monitor-release-puller.py:apps/storage-monitor/deploy/server/storage-monitor-release-puller.py
       storage-release-authorizer.py:scripts/authorize_gpu_release.py)
EXPECTED_TOOL_DIGESTS="$(for t in "${TOOLS[@]}"; do sha256sum "$WORK/src-tools/${t#*:}" | cut -d' ' -f1; done)"
TOOL_NAMES="$(for t in "${TOOLS[@]}"; do printf '%s ' "${t%%:*}"; done)"
# On the real central host, the tools commit must still be what /usr/local/libexec holds (read-only check).
if [[ -r /usr/local/libexec/storage-monitor-release-puller.py.sha256 ]]; then
  live_digests="$(for t in "${TOOLS[@]}"; do cut -d' ' -f1 "/usr/local/libexec/${t%%:*}.sha256"; done)"
  [[ "$live_digests" == "$EXPECTED_TOOL_DIGESTS" ]] || fail "/usr/local/libexec no longer matches tools commit ${TOOLS_SHA:0:7}"
  pass "the seven tools in /usr/local/libexec match ${TOOLS_SHA:0:7} by their .sha256 files"
fi

# -- 4. scenario naive: merge first --------------------------------------------------------
NAIVE="$(boot_central central-naive)"
pass "central-naive: install.sh @ ${LIVE_SHA:0:7}, tools @ ${TOOLS_SHA:0:7} (digests checked), ${PREV_SHA:0:7} bootstrapped by its own builder and the installed activator, the installed puller activated ${LIVE_SHA:0:7}, timer enabled, old store written"
set_main "$NAIVE" "$MERGE_SHA"
rc=0; out="$(puller "$NAIVE")" || rc=$?
[[ $rc -ne 0 ]] || fail "naive: the installed puller accepted the new release: $out"
note "naive: installed puller on the merge: $out"
failed="$(in_c "$NAIVE" 'cat /var/lib/storage-viz-dashboard/puller/failed-release.json')"
note "naive: failed-release.json $failed"
[[ "$(json_get 'd["sha"]' <<<"$failed")" == "$MERGE_SHA" ]] || fail "naive: backoff does not name the merge"
[[ "$(in_c "$NAIVE" 'readlink /opt/storage-viz-dashboard')" == *"$LIVE_SHA"* ]] || fail "naive: the live release changed"
health "$NAIVE" /usr/local/libexec/storage-dashboard-health-check.py || fail "naive: the live release is unhealthy after the failed pull"
out="$(puller "$NAIVE")"
[[ "$out" == backoff ]] || fail "naive: second run is not backoff: $out"
pass "naive: the installed tools reject the new tree; the live release keeps serving; failed-release.json holds the merge and the next run answers backoff"
in_c "$NAIVE" "rm -rf /root/new-main"; docker cp "$WORK/src-new" "$NAIVE:/root/new-main"
in_c "$NAIVE" 'bash /root/new-main/apps/storage-monitor/deploy/server/install-dashboard-deployer.sh >/root/install-new.log'
rc=0; out="$(puller "$NAIVE")" || rc=$?
note "naive: first run after rerunning the installer: $out"
[[ "$out" == *activated* ]] || fail "naive: rerunning the installer does not recover the naive order: $out"
health "$NAIVE" /usr/local/libexec/storage-dashboard-health-check.py || fail "naive: new health check fails after recovery"
pass "naive: rerunning the installer clears the backoff it caused, and the next run activates ${MERGE_SHA:0:7}"
docker rm -f "$NAIVE" >/dev/null

# -- 5. scenario proposed -----------------------------------------------------------------
C="$(boot_central central)"
pass "central: rebuilt in the live state again"
OLD_STORE="$(store_digest "$C")"
declare -A OLD_SNAPSHOT
for host in "${HOSTS[@]}"; do
  OLD_SNAPSHOT[$host]="$(in_c "$C" "python3 -c 'import json; print(json.load(open(\"/var/lib/storage-viz-dashboard/state/$host/current.json\"))[\"snapshot\"])'" | sed -E 's/^snapshot-v2-([0-9a-f]{64})\.json$/\1/')"
done
in_c "$C" 'systemctl stop storage-monitor-release-puller.timer'                                                   # step 1
set_main "$C" "$MERGE_SHA"                                                                                         # step 2
in_c "$C" "rm -rf /root/new-main"; docker cp "$WORK/src-new" "$C:/root/new-main"
in_c "$C" 'bash /root/new-main/apps/storage-monitor/deploy/server/install-dashboard-deployer.sh >/root/install-new.log'   # step 3
in_c "$C" 'test ! -e /var/lib/storage-viz-dashboard/puller/failed-release.json' || fail "proposed: a backoff is pending before the first run"  # step 4
CUTOVER="$(in_c "$C" 'date +%s')"
out="$(puller "$C")" || fail "proposed: puller failed: $out"                                                       # step 5
[[ "$out" == *activated* ]] || fail "proposed: puller did not activate: $out"
in_c "$C" 'systemctl start storage-monitor-release-puller.timer'                                                  # step 6
[[ "$(in_c "$C" 'systemctl is-enabled storage-monitor-release-puller.timer; systemctl is-active storage-monitor-release-puller.timer' | tr '\n' ' ')" == "enabled active " ]] || fail "proposed: timer not enabled and active"
state="$(in_c "$C" 'cat /var/lib/storage-viz-dashboard/activation-state.json')"
[[ "$(json_get 'd["source_sha"] + " " + d["previous"]' <<<"$state")" == "$MERGE_SHA /srv/storage-viz-dashboard/releases/$LIVE_SHA/storage-monitor" ]] || fail "proposed: activation state $state"
health "$C" /usr/local/libexec/storage-dashboard-health-check.py || fail "proposed: new health check fails"
in_c "$C" 'install -m 0755 /root/tools-main/apps/storage-monitor/deploy/server/health-check-dashboard.py /root/old-health-check.py'
health "$C" /root/old-health-check.py || fail "proposed: the old health checker fails against the new release (S9)"
pass "proposed: stop timer, merge, installer from new main, one puller run activated ${MERGE_SHA:0:7} (previous ${LIVE_SHA:0:7}), timer enabled+active, new checker and old checker (S9) pass"

for _ in $(seq 1 60); do
  fleet="$(in_c "$C" 'python3 /rehearsal/rehearsal_probe.py fleet')"
  [[ "$(json_get 'all(s["link"] == "ok" and s["volumes"] and s["freshness"] != "unknown" for s in d["servers"])' <<<"$fleet")" == True ]] && break
  sleep 3
done
note "proposed: fleet $(json_get 'd["servers"]' <<<"$fleet")"
[[ "$(json_get '[s["server_id"] for s in d["servers"]]' <<<"$fleet")" == "['host-a', 'host-b']" ]] || fail "proposed: fleet ids"
[[ "$(json_get 'all(s["link"] == "ok" and [v["mountpoint"] for v in s["volumes"]] == ["/data"] and all(v["total"] and v["used"] is not None for v in s["volumes"]) for s in d["servers"])' <<<"$fleet")" == True ]] \
  || fail "proposed: a host lacks link ok or per-mount capacity, or shows a mount the v1 agent skipped"
[[ "$(json_get 'all(s["freshness"] == "fresh" for s in d["servers"])' <<<"$fleet")" == True ]] || fail "proposed: a v1 host is not fresh"
pass "proposed: /api/v2/fleet lists every host fresh, with link ok and per-mount capacity from the v1 agent (S2/S3), and no skipped mounts"

for host in "${HOSTS[@]}"; do
  record="$(in_c "$C" "cat /var/lib/storage-viz-dashboard/state/servers/$host/record.json")"
  [[ "$(json_get 'd["accepted"].get("legacy")' <<<"$record")" == "${OLD_SNAPSHOT[$host]}" ]] \
    || fail "proposed: $host record holds $(json_get 'd["accepted"]' <<<"$record"), not the old store's ${OLD_SNAPSHOT[$host]}; old store now $(in_c "$C" "cat /var/lib/storage-viz-dashboard/state/$host/current.json"), host publishes $(in_c "${HOST_CID[$host]}" 'cat /var/lib/storage-viz/scan-status.json' | json_get 'd["sha256"]')"
  in_c "$C" "test -f /var/lib/storage-viz-dashboard/state/servers/$host/captures/${OLD_SNAPSHOT[$host]}.json" || fail "proposed: $host capture missing"
done
durations="$(in_c "$C" "cat /var/lib/storage-viz-dashboard/state/servers/${HOSTS[0]}/record.json" | json_get 'd["durations"]')"
sleep 30
opens="$(sftp_snapshot_opens "$CUTOVER")"
[[ "$opens" == 0 ]] || fail "proposed: central downloaded $opens snapshot(s) again after the cutover"
pass "proposed: each record holds the old store's snapshot sha with its capture, durations $durations imported, and no snapshot was downloaded again (C8)"

viewer="$(in_c "$C" 'python3 /rehearsal/rehearsal_probe.py viewer')"
[[ "$(json_get 'd["index_status"] == 200 and d["bad"] == [] and d["assets"] > 5' <<<"$viewer")" == True ]] || fail "proposed: viewer $viewer"
pass "proposed: the viewer page and its $(json_get 'd["assets"]' <<<"$viewer") files load through the proxy byte for byte"

# host-a still carries the imported v1 job's cooldown, so the v2 rescan goes to host-b.
scans_before="$(scan_starts host-b)"
rescan="$(in_c "$C" 'python3 /rehearsal/rehearsal_probe.py rescan --server host-b')"
protocol="$(in_c "$C" 'cat /var/lib/storage-viz-dashboard/state/servers/host-b/record.json' | json_get '(d["last_job"] or {}).get("protocol")')"
note "proposed: rescan $(json_get '{"post": d["post_status"], "polls": d["polls"], "last_job": d["last_job"]}' <<<"$rescan") protocol $protocol"
[[ "$(json_get 'd["post_status"]' <<<"$rescan")" == 202 && "$(scan_starts host-b)" -gt "$scans_before" ]] || fail "proposed: rescan did not start the host's scan unit"
[[ "$(json_get '(d["last_job"] or {}).get("state")' <<<"$rescan")" == succeeded && "$protocol" == invocation ]] || fail "proposed: the v1 rescan did not succeed by invocation correlation"
pass "proposed: a rescan POST on a v1 host started storage-viz-scan.service and its job ended succeeded by InvocationID correlation (S4)"

# -- 6. rollback and roll-forward ------------------------------------------------------------
[[ "$(store_digest "$C")" == "$OLD_STORE" ]] || fail "rollback: the new release changed the old store"
# The runbook keeps the timer stopped around a rollback; here it also keeps timer runs out of the checks.
in_c "$C" 'systemctl stop storage-monitor-release-puller.timer'
in_c "$C" '/usr/local/libexec/storage-dashboard-activate.py --rollback-state --restart-argv /usr/bin/systemctl restart storage-viz-dashboard.service storage-viz-proxy.service' >/dev/null
state="$(in_c "$C" 'cat /var/lib/storage-viz-dashboard/activation-state.json')"
[[ "$(json_get 'd["status"] + " " + d["source_sha"]' <<<"$state")" == "rolled_back $LIVE_SHA" ]] || fail "rollback: state $state"
v1="$(in_c "$C" 'python3 /rehearsal/rehearsal_probe.py servers-v1')"
[[ "$(json_get 'd["status"] == 200 and d["data_mode"] == "inventory" and [s["id"] for s in d["servers"]] == ["host-a", "host-b"] and all(s["snapshot_availability"] == "available" for s in d["servers"])' <<<"$v1")" == True ]] || fail "rollback: old release $v1"
rc_new=0; health "$C" /usr/local/libexec/storage-dashboard-health-check.py || rc_new=$?
rc_old=0; health "$C" /root/old-health-check.py || rc_old=$?
note "rollback: installed (new) health checker exit $rc_new, old checker exit $rc_old against ${LIVE_SHA:0:7}"
[[ $rc_new -eq 0 ]] || fail "rollback: the installed health checker fails against the rolled-back release"
out="$(puller "$C")"
[[ "$out" == already-current ]] || fail "rollback: the next puller run did not keep the rollback: $out"
pass "rollback: --rollback-state put ${LIVE_SHA:0:7} back on its untouched old store (R1), the installed checker passes, and the next puller run keeps it (already-current)"

in_c "$C" 'rm /var/lib/storage-viz-dashboard/puller/current-live-sha'
out="$(puller "$C")" || fail "roll-forward: puller failed: $out"
[[ "$out" == *activated* ]] || fail "roll-forward: $out"
health "$C" /usr/local/libexec/storage-dashboard-health-check.py || fail "roll-forward: health"
fleet="$(in_c "$C" 'python3 /rehearsal/rehearsal_probe.py fleet')"
[[ "$(json_get 'all(s["volumes"] for s in d["servers"])' <<<"$fleet")" == True ]] || fail "roll-forward: fleet $fleet"
pass "roll-forward: removing current-live-sha and one puller run re-activated ${MERGE_SHA:0:7}; health and fleet ok"
echo "rehearsal: all checks passed"
