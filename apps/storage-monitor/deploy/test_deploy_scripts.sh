#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY="$ROOT/deploy"
SERVICE="$DEPLOY/systemd/storage-viz-scan.service.in"
TIMER="$DEPLOY/systemd/storage-viz-scan.timer"
SUDOERS="$DEPLOY/sudoers/storage-viz-monitoring"
INSTALL="$DEPLOY/install-agent.sh"
DEPLOY_SCRIPT="$DEPLOY/deploy-agent.sh"
VERIFY_LINUX="$DEPLOY/verify-linux.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }
assert_file() { [[ -f "$1" ]] || fail "missing file: $1"; }
assert_contains() { local file="$1" text="$2"; grep -Fqx "$text" "$file" || fail "$file missing exact line: $text"; }
assert_grep() { local file="$1" pattern="$2" desc="$3"; grep -Eq "$pattern" "$file" || fail "$file missing $desc ($pattern)"; }
assert_not_grep() { local file="$1" pattern="$2" desc="$3"; ! grep -Eq "$pattern" "$file" || fail "$file contains forbidden $desc ($pattern)"; }
file_mode() {
  if stat -c %a "$1" >/dev/null 2>&1; then
    stat -c %a "$1"
  else
    stat -f %Lp "$1"
  fi
}
cleanup() {
  [[ -z "${VERIFY_TMP:-}" ]] || rm -rf -- "$VERIFY_TMP"
  [[ -z "${TMP:-}" ]] || rm -rf -- "$TMP"
  [[ -z "${VIEWER_SECRET:-}" ]] || rm -f -- "$VIEWER_SECRET"
  [[ -z "${STALE_SCANNER:-}" ]] || rm -f -- "$STALE_SCANNER"
  rm -f -- "$ROOT/output/verification/linux-verification.txt"
  rmdir "$ROOT/output/verification" "$ROOT/output" 2>/dev/null || true
}
trap cleanup EXIT

for f in "$SERVICE" "$TIMER" "$SUDOERS" "$INSTALL" "$DEPLOY_SCRIPT" "$VERIFY_LINUX"; do
  assert_file "$f"
done
pass "all Task 5 deploy assets exist"

assert_grep "$VERIFY_LINUX" 'mktemp -d /tmp/storage-viz-verify\.XXXXXX' "unique temporary Linux verification directory"
assert_grep "$VERIFY_LINUX" 'git ls-files -z' "NUL-safe tracked-files-only transfer contract"
assert_grep "$VERIFY_LINUX" 'scripts/authorize_gpu_release\.py' "shared release authorizer in tracked verification archive"
assert_grep "$VERIFY_LINUX" 'apps/storage-monitor' "monorepo app layout in tracked verification archive"
assert_grep "$VERIFY_LINUX" 'validate_tar_members' "tar member validation before extraction"
assert_grep "$VERIFY_LINUX" 'trap.*rm -rf --' "temporary verification cleanup trap"
assert_grep "$VERIFY_LINUX" 'make -C scanner clean all test' "scanner build/test verification command"
assert_grep "$VERIFY_LINUX" 'deploy/install-agent\.sh --dry-run' "agent installer dry-run verification command"
assert_grep "$VERIFY_LINUX" 'linux-verification\.txt' "repository-owned Linux verification artifact"
assert_grep "$VERIFY_LINUX" '/home/ircv/workspace/monitoring\*' "forbidden GPU Monitor workspace guard"
assert_grep "$VERIFY_LINUX" 'validate_linux_host' "remote host validation"
assert_not_grep "$VERIFY_LINUX" 'sudo|sshpass|expect|(^|[^A-Z])password|/home/ircv/workspace/monitoring[^*]' "sudo, password helpers, or concrete private snapshot paths"
pass "Linux verification wrapper has tracked-files-only temp execution contract"

VERIFY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-verify-test.XXXXXX")"
VERIFY_FAKEBIN="$VERIFY_TMP/bin"
mkdir -p "$VERIFY_FAKEBIN"
cat > "$VERIFY_FAKEBIN/ssh" <<'FAKE'
#!/usr/bin/env bash
printf 'ssh' >> "${VERIFY_FAKE_LOG:?}"
for arg in "$@"; do printf ' <%s>' "$arg" >> "$VERIFY_FAKE_LOG"; done
printf '\n' >> "$VERIFY_FAKE_LOG"
args=" $* "
case "${VERIFY_SCENARIO:-success}" in
  forbidden_workdir)
    if [[ "$args" == *"mktemp -d /tmp/storage-viz-verify.XXXXXX"* ]]; then
      printf 'remote_workdir_guard=rejected\n'
      exit 63
    fi
    ;;
  scp_cleanup_fail|verify_empty_cleanup_fail)
    if [[ "$args" == *"mktemp -d /tmp/storage-viz-verify.XXXXXX"* ]]; then
      printf '/tmp/storage-viz-verify.FAKE01\n'
      exit 0
    fi
    if [[ "$args" == *"VERIFY_TMP="*"bash -s"* ]]; then
      exit 255
    fi
    if [[ "$args" == *"rm -rf --"* ]]; then
      exit 44
    fi
    ;;
  scp_fail|remote_fail|verify_empty_fail|cleanup_fail|success)
    if [[ "$args" == *"mktemp -d /tmp/storage-viz-verify.XXXXXX"* ]]; then
      printf '/tmp/storage-viz-verify.FAKE01\n'
      exit 0
    fi
    if [[ "$args" == *"rm -rf --"* ]]; then
      printf 'cleanup-call\n' >> "${VERIFY_CLEANUP_LOG:?}"
      exit 0
    fi
    if [[ "$args" == *"VERIFY_TMP="*"bash -s"* ]]; then
      case "${VERIFY_SCENARIO:-success}" in
        verify_empty_fail)
          exit 255
          ;;
        remote_fail)
          printf 'command=fake-remote\nexit_code=7\nremote_cleanup=removed\n'
          exit 7
          ;;
        cleanup_fail)
          printf 'command=fake-remote\nexit_code=0\nremote_cleanup=failed\n'
          exit 0
          ;;
        *)
          printf 'command=fake-remote\nexit_code=0\nremote_cleanup=removed\n'
          exit 0
          ;;
      esac
    fi
    ;;
esac
exit 0
FAKE
cat > "$VERIFY_FAKEBIN/scp" <<'FAKE'
#!/usr/bin/env bash
printf 'scp' >> "${VERIFY_FAKE_LOG:?}"
for arg in "$@"; do printf ' <%s>' "$arg" >> "$VERIFY_FAKE_LOG"; done
printf '\n' >> "$VERIFY_FAKE_LOG"
if [[ "${VERIFY_SCENARIO:-success}" == scp_fail || "${VERIFY_SCENARIO:-success}" == scp_cleanup_fail ]]; then
  exit 55
fi
exit 0
FAKE
chmod +x "$VERIFY_FAKEBIN/ssh" "$VERIFY_FAKEBIN/scp"

run_verify_fake() {
  local scenario="$1" host="${2:-good.example}" rc
  rm -f "$VERIFY_TMP/fake.log" "$VERIFY_TMP/cleanup.log" "$ROOT/output/verification/linux-verification.txt"
  set +e
  VERIFY_SCENARIO="$scenario" VERIFY_FAKE_LOG="$VERIFY_TMP/fake.log" VERIFY_CLEANUP_LOG="$VERIFY_TMP/cleanup.log" \
    PATH="$VERIFY_FAKEBIN:$PATH" STORAGE_VIZ_LINUX_HOST="$host" STORAGE_VIZ_LINUX_PORT=2222 \
    "$VERIFY_LINUX" --remote >"$VERIFY_TMP/$scenario.out" 2>"$VERIFY_TMP/$scenario.err"
  rc=$?
  set -e
  printf '%s' "$rc" > "$VERIFY_TMP/$scenario.rc"
}

run_verify_fake success '-oProxyCommand=bad'
[[ "$(cat "$VERIFY_TMP/success.rc")" != "0" ]] || fail "verify-linux accepted option-injection host"
[[ ! -s "$VERIFY_TMP/fake.log" ]] || fail "verify-linux contacted ssh/scp before rejecting unsafe host"
pass "verify-linux rejects unsafe remote host before transport"

run_verify_fake scp_fail
[[ "$(cat "$VERIFY_TMP/scp_fail.rc")" != "0" ]] || fail "verify-linux succeeded after scp failure"
grep -Fq 'remote_cleanup=removed' "$ROOT/output/verification/linux-verification.txt" || fail "scp failure did not record truthful remote cleanup result"
grep -Fq 'overall_exit_code=2' "$ROOT/output/verification/linux-verification.txt" || fail "scp failure did not record nonzero overall result"
pass "verify-linux records cleanup after scp failure"

run_verify_fake scp_cleanup_fail
[[ "$(cat "$VERIFY_TMP/scp_cleanup_fail.rc")" != "0" ]] || fail "verify-linux succeeded after scp cleanup failure"
grep -Fq 'remote_cleanup=failed' "$ROOT/output/verification/linux-verification.txt" || fail "scp cleanup failure was not recorded"
! grep -Fq 'overall_exit_code=0' "$ROOT/output/verification/linux-verification.txt" || fail "scp cleanup failure produced zero overall result"
pass "verify-linux records scp cleanup failure"

run_verify_fake remote_fail
[[ "$(cat "$VERIFY_TMP/remote_fail.rc")" != "0" ]] || fail "verify-linux succeeded after remote command failure"
grep -Fq 'remote_cleanup=removed' "$ROOT/output/verification/linux-verification.txt" || fail "remote command failure did not preserve cleanup result"
grep -Fq 'overall_exit_code=7' "$ROOT/output/verification/linux-verification.txt" || fail "remote command failure did not record nonzero overall result"
pass "verify-linux records cleanup after remote command failure"

run_verify_fake verify_empty_fail
[[ "$(cat "$VERIFY_TMP/verify_empty_fail.rc")" != "0" ]] || fail "verify-linux succeeded after empty verification ssh failure"
grep -Fq 'remote_cleanup=removed' "$ROOT/output/verification/linux-verification.txt" || fail "empty verification ssh failure did not run fallback cleanup"
grep -Fq 'cleanup-call' "$VERIFY_TMP/cleanup.log" || fail "empty verification ssh failure did not invoke fallback cleanup ssh"
grep -Fq 'overall_exit_code=255' "$ROOT/output/verification/linux-verification.txt" || fail "empty verification ssh failure did not preserve original nonzero exit"
pass "verify-linux falls back to cleanup after empty verification ssh failure"

run_verify_fake verify_empty_cleanup_fail
[[ "$(cat "$VERIFY_TMP/verify_empty_cleanup_fail.rc")" != "0" ]] || fail "verify-linux succeeded after empty verification ssh and cleanup failure"
grep -Fq 'remote_cleanup=failed' "$ROOT/output/verification/linux-verification.txt" || fail "empty verification ssh cleanup failure was not recorded"
! grep -Fq 'overall_exit_code=0' "$ROOT/output/verification/linux-verification.txt" || fail "empty verification ssh cleanup failure produced zero overall result"
pass "verify-linux records fallback cleanup failure after empty verification ssh failure"

run_verify_fake forbidden_workdir
[[ "$(cat "$VERIFY_TMP/forbidden_workdir.rc")" != "0" ]] || fail "verify-linux succeeded from forbidden remote workdir"
grep -Fq 'remote_workdir_guard=rejected' "$ROOT/output/verification/linux-verification.txt" || fail "forbidden workdir rejection was not recorded"
grep -Fq 'overall_exit_code=63' "$ROOT/output/verification/linux-verification.txt" || fail "forbidden workdir did not record guard exit code"
pass "verify-linux records forbidden remote workdir rejection"

run_verify_fake cleanup_fail
[[ "$(cat "$VERIFY_TMP/cleanup_fail.rc")" != "0" ]] || fail "verify-linux succeeded when remote cleanup failed"
grep -Fq 'remote_cleanup=failed' "$ROOT/output/verification/linux-verification.txt" || fail "cleanup failure was not recorded"
! grep -Fq 'overall_exit_code=0' "$ROOT/output/verification/linux-verification.txt" || fail "cleanup failure produced zero overall result"
pass "verify-linux fails when remote cleanup fails"

assert_contains "$TIMER" "OnUnitActiveSec=6h"
assert_contains "$TIMER" "Persistent=true"
assert_contains "$TIMER" "RandomizedDelaySec=30m"
pass "timer has six-hour persistent randomized cadence"

assert_contains "$SERVICE" "User=root"
assert_contains "$SERVICE" "Group=storage-viz-collector"
assert_contains "$SERVICE" "UMask=0027"
assert_contains "$SERVICE" "Nice=19"
assert_contains "$SERVICE" "IOSchedulingClass=idle"
assert_contains "$SERVICE" "NoNewPrivileges=yes"
assert_contains "$SERVICE" "ProtectSystem=strict"
assert_contains "$SERVICE" "ProtectHome=read-only"
assert_contains "$SERVICE" "ReadWritePaths=/var/lib/storage-viz /run/storage-viz"
assert_contains "$SERVICE" "PrivateTmp=yes"
assert_contains "$SERVICE" "ProtectKernelTunables=yes"
assert_contains "$SERVICE" "ProtectKernelModules=yes"
assert_contains "$SERVICE" "ProtectControlGroups=yes"
assert_contains "$SERVICE" "RestrictAddressFamilies=AF_UNIX"
assert_contains "$SERVICE" "CapabilityBoundingSet=CAP_DAC_READ_SEARCH"
assert_contains "$SERVICE" "AmbientCapabilities=CAP_DAC_READ_SEARCH"
assert_not_grep "$SERVICE" 'CAP_SYS_ADMIN|CAP_DAC_OVERRIDE|CAP_SYS_PTRACE|CAP_NET|CAP_CHOWN|CAP_FOWNER' "broad/admin/network/write capabilities"
assert_contains "$SERVICE" "ExecStart=/usr/bin/python3 -m agent.scan_runner --config /etc/storage-viz/scanner.yaml"
assert_contains "$SERVICE" "RuntimeDirectory=storage-viz"
assert_contains "$SERVICE" "RuntimeDirectoryMode=0750"
assert_not_grep "$SERVICE" 'flock|scan\.lock' "outer systemd/flock lock; agent.scan_runner owns nonblocking lock"
grep -Fq "lock_path = run_dir / \"scan.lock\"" "$ROOT/agent/scan_runner.py" || fail "agent.scan_runner lock contract not found"
grep -Fq "if not try_lock_fd(lock_fd):" "$ROOT/agent/scan_runner.py" || fail "agent.scan_runner nonblocking lock not found"
pass "service invokes scan_runner directly; runner owns one nonblocking lock layer"

expected_sudoers='monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service'
actual_sudoers="$(grep -Ev '^[[:space:]]*(#|$)' "$SUDOERS")"
[[ "$actual_sudoers" == "$expected_sudoers" ]] || fail "sudoers command mismatch: <$actual_sudoers>"
assert_not_grep "$SUDOERS" 'ALL$|systemctl[[:space:]]+(restart|enable|daemon-reload|stop)|/bin/sh|bash|sudoedit' "general sudo privileges"
pass "sudoers permits only exact noninteractive service start"

assert_grep "$INSTALL" 'DRY_RUN' "dry-run implementation"
assert_grep "$INSTALL" '^[[:space:]]*if[[:space:]].*EUID.*-ne[[:space:]]+0' "real install root check"
assert_grep "$INSTALL" 'SERVER_ID.*\^\[A-Za-z0-9_.-\]\+\$|unsafe server_id; expected \^\[A-Za-z0-9_.-\]\+\$' "safe server id validation"
assert_not_grep "$INSTALL" 'targets|mount overrides|SCAN_TARGETS' "arbitrary scan target override"
assert_grep "$INSTALL" 'atomic_write[[:space:]]+"\$CONFIG_FILE"[[:space:]]+0644[[:space:]]+root:root' "root:root 0644 scanner config install"
assert_not_grep "$INSTALL" 'useradd|nologin' "monitoring account creation or nologin shell mutation"
assert_grep "$INSTALL" 'id[[:space:]]+"\$MONITORING_USER"' "monitoring account checked before group creation"
assert_grep "$INSTALL" 'make[[:space:]].*clean.*hstscan|make[[:space:]].*hstscan.*clean' "real install rebuilds scanner from source"
pass "install script exposes dry-run/root/safe-config contract"

assert_grep "$DEPLOY_SCRIPT" 'BatchMode=yes' "BatchMode monitoring checks"
assert_grep "$DEPLOY_SCRIPT" 'StrictHostKeyChecking=yes' "strict host key checking"
assert_grep "$DEPLOY_SCRIPT" 'IdentitiesOnly=yes' "explicit identity only"
assert_grep "$DEPLOY_SCRIPT" 'UserKnownHostsFile=' "explicit known-hosts file"
assert_grep "$DEPLOY_SCRIPT" 'ConnectTimeout=' "bounded SSH timeout"
assert_grep "$DEPLOY_SCRIPT" 'LC_ALL=C[[:space:]]+sudo[[:space:]]+-n[[:space:]]+-l' "full side-effect-free monitoring sudo policy listing"
assert_grep "$DEPLOY_SCRIPT" 'parse_monitoring_sudo_policy|approved_entry_count' "strict sudo policy parser"
assert_grep "$DEPLOY_SCRIPT" 'mktemp -d /tmp/storage-viz-agent-bootstrap\.XXXXXX' "remote private mktemp bootstrap dir"
assert_grep "$DEPLOY_SCRIPT" 'umask 077' "remote private temp umask"
assert_grep "$DEPLOY_SCRIPT" 'trap.*rm -rf --' "remote trapped cleanup"
assert_grep "$DEPLOY_SCRIPT" 'scanner/hstscan' "scanner executable archive exclusion"
assert_grep "$DEPLOY_SCRIPT" 'START_SCAN' "explicit opt-in scan start flag"
assert_not_grep "$DEPLOY_SCRIPT" 'eval|sshpass|expect|(^|[^A-Z])password|echo.*sudo|tee.*password|BatchMode=no.*monitoring' "password handling or unsafe shell evaluation"
pass "deploy script has hardened SSH/static sudo contract"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/storage-viz-deploy-test.XXXXXX")"

FAKEBIN="$TMP/bin"
mkdir -p "$FAKEBIN"
cat > "$FAKEBIN/systemd-analyze" <<'FAKE'
#!/usr/bin/env bash
printf 'systemd-analyze %q\n' "$@" >> "${FAKE_LOG:?}"
exit 0
FAKE
cat > "$FAKEBIN/visudo" <<'FAKE'
#!/usr/bin/env bash
printf 'visudo %q\n' "$@" >> "${FAKE_LOG:?}"
exit 0
FAKE
cat > "$FAKEBIN/systemctl" <<'FAKE'
#!/usr/bin/env bash
echo "systemctl must not run in dry-run" >&2
exit 97
FAKE
chmod +x "$FAKEBIN"/*

DRY_PREFIX="$TMP/dry-prefix"
FAKE_LOG="$TMP/install.log" PATH="$FAKEBIN:$PATH" "$INSTALL" --dry-run --prefix "$DRY_PREFIX" --server-id host-a >"$TMP/install.out"
[[ -f "$DRY_PREFIX/etc/storage-viz/scanner.yaml" ]] || fail "dry-run did not render scanner config under temp prefix"
[[ -f "$DRY_PREFIX/etc/systemd/system/storage-viz-scan.service" ]] || fail "dry-run did not render service under temp prefix"
[[ -f "$DRY_PREFIX/etc/systemd/system/storage-viz-scan.timer" ]] || fail "dry-run did not render timer under temp prefix"
[[ -f "$DRY_PREFIX/etc/sudoers.d/storage-viz-monitoring" ]] || fail "dry-run did not render sudoers under temp prefix"
python3 -m json.tool "$DRY_PREFIX/etc/storage-viz/scanner.yaml" >/dev/null || fail "scanner config is not strict JSON-compatible"
[[ "$(file_mode "$DRY_PREFIX/etc/storage-viz/scanner.yaml")" == "644" ]] || fail "scanner config mode is not 0644"
! grep -q 'systemctl must not run' "$TMP/install.out" || fail "dry-run attempted systemctl"
grep -q 'systemd-analyze' "$TMP/install.log" || fail "dry-run did not validate units when systemd-analyze is available"
grep -q 'visudo' "$TMP/install.log" || fail "dry-run did not validate sudoers when visudo is available"
pass "install dry-run renders/validates temp output without systemctl"

BAD_PREFIX="$TMP/bad-prefix"
if "$INSTALL" --dry-run --prefix "$BAD_PREFIX" --server-id '../bad' >"$TMP/bad.out" 2>"$TMP/bad.err"; then
  fail "install accepted unsafe server id"
fi
pass "install rejects unsafe server ids"

STALE_SCANNER="$ROOT/scanner/hstscan"
rm -f "$STALE_SCANNER"
printf 'stale scanner\n' > "$STALE_SCANNER"
chmod +x "$STALE_SCANNER"
cat > "$FAKEBIN/id" <<'FAKE'
#!/usr/bin/env bash
printf 'id %s\n' "$*" >> "${FAKE_LOG:?}"
[[ "$1" == "monitoring" ]]
FAKE
cat > "$FAKEBIN/getent" <<'FAKE'
#!/usr/bin/env bash
printf 'getent %s\n' "$*" >> "${FAKE_LOG:?}"
exit 1
FAKE
cat > "$FAKEBIN/groupadd" <<'FAKE'
#!/usr/bin/env bash
printf 'groupadd %s\n' "$*" >> "${FAKE_LOG:?}"
exit 0
FAKE
cat > "$FAKEBIN/usermod" <<'FAKE'
#!/usr/bin/env bash
printf 'usermod %s\n' "$*" >> "${FAKE_LOG:?}"
exit 0
FAKE
cat > "$FAKEBIN/chown" <<'FAKE'
#!/usr/bin/env bash
printf 'chown %s\n' "$*" >> "${FAKE_LOG:?}"
exit 0
FAKE
cat > "$FAKEBIN/make" <<'FAKE'
#!/usr/bin/env bash
printf 'make %s\n' "$*" >> "${FAKE_LOG:?}"
dir="."
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-C" ]]; then dir="$2"; shift 2; continue; fi
  shift
done
printf '#!/usr/bin/env sh\necho fresh scanner\n' > "$dir/hstscan"
chmod +x "$dir/hstscan"
exit 0
FAKE
cat > "$FAKEBIN/systemctl" <<'FAKE'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "${FAKE_LOG:?}"
exit 0
FAKE
chmod +x "$FAKEBIN/id" "$FAKEBIN/getent" "$FAKEBIN/groupadd" "$FAKEBIN/usermod" "$FAKEBIN/chown" "$FAKEBIN/make" "$FAKEBIN/systemctl"
REAL_PREFIX="$TMP/real-prefix"
FAKE_LOG="$TMP/real-install.log" PATH="$FAKEBIN:$PATH" SYSTEMCTL="$FAKEBIN/systemctl" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 "$INSTALL" --prefix "$REAL_PREFIX" --server-id host-a >"$TMP/real-install.out"
grep -q 'make .* clean hstscan' "$TMP/real-install.log" || fail "real install did not rebuild scanner with clean hstscan"
grep -q 'fresh scanner' "$REAL_PREFIX/opt/storage-viz/scanner/hstscan" || fail "real install copied stale scanner instead of rebuilt target binary"
grep -q '^systemctl enable --now storage-viz-scan.timer$' "$TMP/real-install.log" || fail "real install did not enable and start scan timer"
grep -q 'timer enabled and started' "$TMP/real-install.out" || fail "real install reported stale initial-scan status"
id_line="$(grep -n '^id monitoring' "$TMP/real-install.log" | head -1 | cut -d: -f1)"
group_line="$(grep -n '^groupadd' "$TMP/real-install.log" | head -1 | cut -d: -f1)"
[[ -n "$id_line" && -n "$group_line" && "$id_line" -lt "$group_line" ]] || fail "installer did not verify monitoring user before creating group"
pass "real install test mode rebuilds scanner and checks monitoring before group creation"

cat > "$FAKEBIN/ssh" <<'FAKE'
#!/usr/bin/env bash
printf 'ssh' >> "${FAKE_LOG:?}"
for arg in "$@"; do printf ' <%s>' "$arg" >> "$FAKE_LOG"; done
printf '\n' >> "$FAKE_LOG"
exit 0
FAKE
cat > "$FAKEBIN/scp" <<'FAKE'
#!/usr/bin/env bash
printf 'scp' >> "${FAKE_LOG:?}"
for arg in "$@"; do printf ' <%s>' "$arg" >> "$FAKE_LOG"; done
printf '\n' >> "$FAKE_LOG"
if [[ -n "${FAKE_ARCHIVE_COPY:-}" && "${SCP_RC:-0}" == "0" ]]; then
  src="${@: -2:1}"
  cp "$src" "$FAKE_ARCHIVE_COPY"
fi
exit "${SCP_RC:-0}"
FAKE
chmod +x "$FAKEBIN/ssh" "$FAKEBIN/scp"

ID="$TMP/storage-viz_ed25519"
KH="$TMP/storage-viz_known_hosts"
touch "$ID" "$KH"
FAKE_LOG="$TMP/deploy.log" PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --dry-run --host host-a.example --port 2222 --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/deploy.out"
grep -Fq -- '-o StrictHostKeyChecking=yes' "$TMP/deploy.out" || fail "dry-run plan missing strict host key option"
grep -Fq -- '-o BatchMode=yes' "$TMP/deploy.out" || fail "dry-run monitoring plan missing BatchMode"
grep -Fq -- '-o IdentitiesOnly=yes' "$TMP/deploy.out" || fail "dry-run plan missing IdentitiesOnly"
grep -Fq -- '-o ConnectTimeout=' "$TMP/deploy.out" || fail "dry-run plan missing connect timeout"
grep -Fq -- 'monitoring@host-a.example' "$TMP/deploy.out" || fail "dry-run plan missing default monitoring user"
grep -Fq -- 'LC_ALL=C sudo -n -l' "$TMP/deploy.out" || fail "dry-run plan missing full side-effect-free sudo policy listing"
! grep -Fq -- "$ID" "$TMP/deploy.out" || fail "dry-run leaked identity file path"
! grep -Fq -- "$KH" "$TMP/deploy.out" || fail "dry-run leaked known-hosts file path"
grep -Fq -- 'UserKnownHostsFile=\[known-hosts-file\]' "$TMP/deploy.out" || fail "dry-run missing redacted known-hosts option"
grep -Fq -- '-i \[identity-file\]' "$TMP/deploy.out" || fail "dry-run missing redacted identity option"
[[ ! -s "$TMP/deploy.log" ]] || fail "deploy dry-run contacted ssh/scp"
assert_not_grep "$TMP/deploy.out" '(^|[^A-Z])password|sshpass|expect' "password handling in dry-run output"
pass "deploy dry-run prints redacted hardened argv and contacts nothing"

if "$DEPLOY_SCRIPT" --dry-run --host host-a.example --identity-file relative_id --known-hosts-file "$KH" >"$TMP/rel-id.out" 2>"$TMP/rel-id.err"; then
  fail "deploy accepted relative identity path"
fi
if "$DEPLOY_SCRIPT" --dry-run --host host-a.example --identity-file "$ID" --known-hosts-file relative_hosts >"$TMP/rel-kh.out" 2>"$TMP/rel-kh.err"; then
  fail "deploy accepted relative known-hosts path"
fi
VIEWER_SECRET="$ROOT/viewer/storage-viz-test-secret"
touch "$VIEWER_SECRET"
if "$DEPLOY_SCRIPT" --dry-run --host host-a.example --identity-file "$VIEWER_SECRET" --known-hosts-file "$KH" >"$TMP/viewer-id.out" 2>"$TMP/viewer-id.err"; then
  fail "deploy accepted identity under viewer web root"
fi
LINK_TO_VIEWER="$TMP/link-known-hosts"
ln -s "$VIEWER_SECRET" "$LINK_TO_VIEWER"
if "$DEPLOY_SCRIPT" --dry-run --host host-a.example --identity-file "$ID" --known-hosts-file "$LINK_TO_VIEWER" >"$TMP/viewer-kh.out" 2>"$TMP/viewer-kh.err"; then
  fail "deploy accepted known-hosts symlink into viewer web root"
fi
pass "deploy requires absolute regular identity/known-hosts files outside viewer root"

if "$DEPLOY_SCRIPT" --dry-run --host 'bad;host' --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/bad-host.out" 2>"$TMP/bad-host.err"; then
  fail "deploy accepted invalid host"
fi
if "$DEPLOY_SCRIPT" --dry-run --host host-a.example --port 70000 --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/bad-port.out" 2>"$TMP/bad-port.err"; then
  fail "deploy accepted invalid port"
fi
if "$DEPLOY_SCRIPT" --dry-run --host bad-.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/bad-label-end.out" 2>"$TMP/bad-label-end.err"; then
  fail "deploy accepted host label ending hyphen"
fi
if "$DEPLOY_SCRIPT" --dry-run --host -bad.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/bad-label-start.out" 2>"$TMP/bad-label-start.err"; then
  fail "deploy accepted host label starting hyphen"
fi
OVERLONG_LABEL="$(printf 'a%.0s' {1..64}).example"
if "$DEPLOY_SCRIPT" --dry-run --host "$OVERLONG_LABEL" --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/bad-label-len.out" 2>"$TMP/bad-label-len.err"; then
  fail "deploy accepted overlong host label"
fi
"$DEPLOY_SCRIPT" --dry-run --host 192.0.2.10 --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/ipv4.out" || fail "deploy rejected valid IPv4 host"
pass "deploy rejects invalid host labels/ports and keeps IPv4 support"

cat > "$FAKEBIN/ssh" <<'FAKE'
#!/usr/bin/env bash
log="${FAKE_LOG:?}"
printf 'ssh' >> "$log"
for arg in "$@"; do printf ' <%s>' "$arg" >> "$log"; done
printf '\n' >> "$log"
args=" $* "
if [[ "$args" == *"umask 077; mktemp -d /tmp/storage-viz-agent-bootstrap.XXXXXX"* ]]; then
  printf '%s\n' "${REMOTE_MKTEMP_OUTPUT:-/tmp/storage-viz-agent-bootstrap.ABC123}"
  exit 0
fi
if [[ "$args" == *"deploy/install-agent.sh"* ]]; then
  if [[ "${INSTALL_FAIL:-0}" == "1" ]]; then
    printf 'cleanup\n' >> "${FAKE_CLEANUP_LOG:-$log}"
    exit 44
  fi
  exit 0
fi
if [[ "$args" == *"rm -rf --"* ]]; then
  printf 'cleanup\n' >> "${FAKE_CLEANUP_LOG:-$log}"
  exit 0
fi
policy_for() {
  case "$1" in
    exact)
      cat <<'POLICY'
Matching Defaults entries for monitoring on host-a:
    env_reset

User monitoring may run the following commands on host-a:
    (root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service
POLICY
      ;;
    broad)
      cat <<'POLICY'
User monitoring may run the following commands on host-a:
    (ALL) NOPASSWD: ALL
POLICY
      ;;
    extra_shell)
      cat <<'POLICY'
User monitoring may run the following commands on host-a:
    (root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service
    (root) NOPASSWD: /bin/sh
POLICY
      ;;
    systemctl_wild)
      cat <<'POLICY'
User monitoring may run the following commands on host-a:
    (root) NOPASSWD: /usr/bin/systemctl *
POLICY
      ;;
    none)
      return 1
      ;;
  esac
}
if [[ "$args" == *"LC_ALL=C sudo -n -l"* ]]; then
  count_file="${FAKE_CHECK_COUNT_FILE:-}"
  count=1
  if [[ -n "$count_file" ]]; then
    count=0; [[ -f "$count_file" ]] && count="$(cat "$count_file")"
    count=$((count + 1)); printf '%s' "$count" > "$count_file"
  fi
  sequence="${POLICY_SEQUENCE:-exact}"
  IFS=',' read -r -a policies <<< "$sequence"
  index=$((count - 1))
  policy="${policies[$index]:-${policies[-1]}}"
  policy_for "$policy"
  exit $?
fi
if [[ "$args" == *"sudo -n /usr/bin/systemctl start storage-viz-scan.service"* ]]; then
  exit "${START_RC:-0}"
fi
exit 0
FAKE
cat > "$FAKEBIN/tar" <<'FAKE'
#!/usr/bin/env bash
exec /usr/bin/tar "$@"
FAKE
chmod +x "$FAKEBIN/tar"
chmod +x "$FAKEBIN/ssh"

FAKE_LOG="$TMP/permitted.log" POLICY_SEQUENCE=exact START_RC=0 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/permitted.out"
grep -q '<LC_ALL=C> <sudo> <-n> <-l>' "$TMP/permitted.log" || fail "permitted branch did not request full sudo policy listing"
! grep -q '<sudo> <-n> </usr/bin/systemctl> <start> <storage-viz-scan.service>' "$TMP/permitted.log" || fail "permitted branch started scan by default"
! grep -q 'shchoi@host-a.example' "$TMP/permitted.log" || fail "permitted branch used admin SSH"
! grep -q '^scp' "$TMP/permitted.log" || fail "permitted branch used scp"
pass "exact-only sudo listing is accepted without scan start or admin path"

FAKE_LOG="$TMP/broad-initial.log" FAKE_CHECK_COUNT_FILE="$TMP/broad-initial.count" POLICY_SEQUENCE=broad,exact START_RC=0 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/broad-initial.out" 2>"$TMP/broad-initial.err"
grep -q 'shchoi@host-a.example' "$TMP/broad-initial.log" || fail "broad initial policy did not trigger admin bootstrap"
grep -q '^scp' "$TMP/broad-initial.log" || fail "broad initial policy did not copy bootstrap archive"
! grep -q 'NOPASSWD: ALL' "$TMP/broad-initial.out" "$TMP/broad-initial.err" || fail "broad initial policy leaked full sudo listing"
pass "broad initial sudo policy is rejected and leads to bootstrap"

FAKE_LOG="$TMP/extra-shell.log" FAKE_CHECK_COUNT_FILE="$TMP/extra-shell.count" POLICY_SEQUENCE=extra_shell,exact START_RC=0 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/extra-shell.out" 2>"$TMP/extra-shell.err"
grep -q 'shchoi@host-a.example' "$TMP/extra-shell.log" || fail "additional broad/root shell policy did not trigger admin bootstrap"
! grep -q '/bin/sh' "$TMP/extra-shell.out" "$TMP/extra-shell.err" || fail "additional shell policy leaked full sudo listing"
pass "exact plus broad/root shell sudo policy is rejected"

if FAKE_LOG="$TMP/post-broad.log" FAKE_CHECK_COUNT_FILE="$TMP/post-broad.count" POLICY_SEQUENCE=none,broad START_RC=0 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/post-broad.out" 2>"$TMP/post-broad.err"; then
  fail "post-bootstrap broad sudo policy was accepted"
fi
grep -q 'shchoi@host-a.example' "$TMP/post-broad.log" || fail "post-bootstrap broad test did not run admin bootstrap"
! grep -q '<sudo> <-n> </usr/bin/systemctl> <start> <storage-viz-scan.service>' "$TMP/post-broad.log" || fail "post-bootstrap broad failure started scan"
! grep -q 'NOPASSWD: ALL' "$TMP/post-broad.out" "$TMP/post-broad.err" || fail "post-bootstrap broad policy leaked full sudo listing"
pass "post-bootstrap broad sudo policy is rejected without runtime fallback"

if FAKE_LOG="$TMP/start-fail.log" POLICY_SEQUENCE=exact START_RC=42 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" --start-scan >"$TMP/start-fail.out" 2>"$TMP/start-fail.err"; then
  fail "explicit start failure did not fail deploy"
fi
! grep -q 'shchoi@host-a.example' "$TMP/start-fail.log" || fail "explicit start failure fell back to admin"
pass "explicit scan start failure does not admin-fallback"

FAKE_LOG="$TMP/bootstrap.log" FAKE_CHECK_COUNT_FILE="$TMP/bootstrap.count" POLICY_SEQUENCE=none,exact START_RC=0 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/bootstrap.out" 2>"$TMP/bootstrap.err"
grep -q 'shchoi@host-a.example' "$TMP/bootstrap.log" || fail "missing permission branch did not use admin bootstrap"
grep -q '^scp' "$TMP/bootstrap.log" || fail "missing permission branch did not copy bootstrap archive"
[[ "$(grep -c '<LC_ALL=C> <sudo> <-n> <-l>' "$TMP/bootstrap.log")" -ge 2 ]] || fail "missing permission branch did not recheck full policy listing"
! grep -q '<sudo> <-n> </usr/bin/systemctl> <start> <storage-viz-scan.service>' "$TMP/bootstrap.log" || fail "missing permission branch started scan by default"
pass "missing permission branch bootstraps once then rechecks full policy listing without scan start"

if FAKE_LOG="$TMP/install-fail.log" FAKE_CLEANUP_LOG="$TMP/install-fail.cleanup" FAKE_CHECK_COUNT_FILE="$TMP/install-fail.count" POLICY_SEQUENCE=none,exact INSTALL_FAIL=1 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/install-fail.out" 2>"$TMP/install-fail.err"; then
  fail "bootstrap install failure did not fail deploy"
fi
grep -q 'umask 077; mktemp -d /tmp/storage-viz-agent-bootstrap.XXXXXX' "$TMP/install-fail.log" || fail "bootstrap did not create private remote temp dir"
grep -q 'trap' "$TMP/install-fail.log" || fail "bootstrap install did not use remote trap cleanup"
grep -q 'cleanup' "$TMP/install-fail.cleanup" "$TMP/install-fail.log" || fail "bootstrap install failure did not attempt cleanup"
pass "bootstrap install failure attempts remote cleanup with private temp"

if FAKE_LOG="$TMP/scp-fail.log" FAKE_CLEANUP_LOG="$TMP/scp-fail.cleanup" FAKE_CHECK_COUNT_FILE="$TMP/scp-fail.count" POLICY_SEQUENCE=none,exact SCP_RC=55 PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/scp-fail.out" 2>"$TMP/scp-fail.err"; then
  fail "scp failure did not fail deploy"
fi
grep -q 'cleanup' "$TMP/scp-fail.cleanup" "$TMP/scp-fail.log" || fail "scp failure did not attempt best-effort remote cleanup"
pass "scp failure attempts best-effort cleanup of validated remote temp"

FAKE_LOG="$TMP/archive.log" FAKE_CHECK_COUNT_FILE="$TMP/archive.count" POLICY_SEQUENCE=none,exact FAKE_ARCHIVE_COPY="$TMP/archive.tar" PATH="$FAKEBIN:$PATH" "$DEPLOY_SCRIPT" --host host-a.example --identity-file "$ID" --known-hosts-file "$KH" >"$TMP/archive.out" 2>"$TMP/archive.err"
[[ -f "$TMP/archive.tar" ]] || fail "fake scp did not capture deployment archive"
! tar -tzf "$TMP/archive.tar" | grep -qx 'scanner/hstscan' || fail "deployment archive included scanner/hstscan executable"
pass "deployment archive excludes scanner/hstscan executable"


# Task 6 storage dashboard live auto-deployer bootstrap contracts.
DASHBOARD_DEPLOYER="$DEPLOY/server/install-dashboard-deployer.sh"
STORAGE_PULLER_SERVICE="$DEPLOY/server/systemd/storage-monitor-release-puller.service"
STORAGE_PULLER_TIMER="$DEPLOY/server/systemd/storage-monitor-release-puller.timer"
STORAGE_PROXY_SERVICE="$DEPLOY/server/systemd/storage-viz-proxy.service"
STORAGE_PULLER="$DEPLOY/server/storage-monitor-release-puller.py"
STORAGE_ACTIVATOR="$DEPLOY/server/activate-dashboard-release.py"
STORAGE_HEALTH="$DEPLOY/server/health-check-dashboard.py"
STORAGE_PROXY_LAUNCHER="$DEPLOY/server/storage-viz-proxy-launcher.py"
STORAGE_AUTHORIZER="$(cd "$ROOT/../.." && pwd)/scripts/authorize_gpu_release.py"
STORAGE_BUILDER="$DEPLOY/build-dashboard-release.py"
for f in "$DASHBOARD_DEPLOYER" "$STORAGE_PULLER_SERVICE" "$STORAGE_PULLER_TIMER" "$STORAGE_PROXY_SERVICE" "$STORAGE_PULLER" "$STORAGE_ACTIVATOR" "$STORAGE_HEALTH" "$STORAGE_PROXY_LAUNCHER" "$STORAGE_AUTHORIZER" "$STORAGE_BUILDER"; do
  assert_file "$f"
done
pass "Task 6 storage dashboard deployer assets exist"

assert_grep "$DASHBOARD_DEPLOYER" 'DRY_RUN' "dashboard deployer dry-run implementation"
assert_grep "$DASHBOARD_DEPLOYER" '^[[:space:]]*if[[:space:]].*EUID.*-ne[[:space:]]+0' "real deployer root check"
assert_grep "$DASHBOARD_DEPLOYER" 'storage-viz-builder' "unprivileged builder identity"
assert_grep "$DASHBOARD_DEPLOYER" 'RUNTIME_USER="storage-viz"' "existing dashboard runtime user is preserved"
assert_grep "$DASHBOARD_DEPLOYER" 'RUNTIME_GROUP="storage-viz"' "existing dashboard runtime group is preserved"
assert_grep "$DASHBOARD_DEPLOYER" '/srv/storage-viz-dashboard/releases' "Storage-only release root"
assert_grep "$DASHBOARD_DEPLOYER" '/var/lib/storage-viz-dashboard/\{puller,builder,data,state\}|/var/lib/storage-viz-dashboard/puller' "Storage-only state paths"
assert_grep "$DASHBOARD_DEPLOYER" '/etc/storage-viz' "Storage-only config path"
assert_grep "$DASHBOARD_DEPLOYER" '/usr/local/libexec/storage-' "Storage-owned libexec namespace"
assert_grep "$DASHBOARD_DEPLOYER" 'storage-release-authorizer\.py' "Storage-owned authorizer copy"
assert_grep "$DASHBOARD_DEPLOYER" 'sha256sum|shasum -a 256|sha256_file' "exact hash verification"
assert_not_grep "$DASHBOARD_DEPLOYER" '/opt/gpu-monitor|/var/lib/gpu-monitor|/etc/gpu-monitor|gpu-monitor-(backend|frontend|bridge|builder)|gpu-deploy-(live|dev)|5173|5174|8000|8001|8100|8101|storage-viz-scan\.(service|timer)' "GPU runtime or remote scanner coupling"
pass "dashboard deployer is Storage-owned and avoids GPU/runtime scanner coupling"

DASH_DRY_PREFIX="$TMP/dashboard-dry-prefix"
DASH_LOG="$TMP/dashboard-dry.log"
cat > "$FAKEBIN/systemctl" <<'FAKE'
#!/usr/bin/env bash
echo "systemctl must not run in dashboard dry-run" >&2
exit 97
FAKE
cat > "$FAKEBIN/useradd" <<'FAKE'
#!/usr/bin/env bash
echo "useradd must not run in dashboard dry-run" >&2
exit 98
FAKE
cat > "$FAKEBIN/chown" <<'FAKE'
#!/usr/bin/env bash
echo "chown must not run in dashboard dry-run" >&2
exit 99
FAKE
chmod +x "$FAKEBIN/systemctl" "$FAKEBIN/useradd" "$FAKEBIN/chown"
PATH="$FAKEBIN:$PATH" "$DASHBOARD_DEPLOYER" --dry-run --prefix "$DASH_DRY_PREFIX" >"$DASH_LOG"
[[ -f "$DASH_DRY_PREFIX/usr/local/libexec/storage-dashboard-build-release.py" ]] || fail "dashboard dry-run did not render builder"
[[ -f "$DASH_DRY_PREFIX/usr/local/libexec/storage-dashboard-activate.py" ]] || fail "dashboard dry-run did not render activator"
[[ -f "$DASH_DRY_PREFIX/usr/local/libexec/storage-dashboard-health-check.py" ]] || fail "dashboard dry-run did not render health checker"
[[ -f "$DASH_DRY_PREFIX/usr/local/libexec/storage-viz-proxy-launcher.py" ]] || fail "dashboard dry-run did not render proxy launcher"
[[ -f "$DASH_DRY_PREFIX/usr/local/libexec/storage-monitor-release-puller.py" ]] || fail "dashboard dry-run did not render puller"
[[ -f "$DASH_DRY_PREFIX/usr/local/libexec/storage-release-authorizer.py" ]] || fail "dashboard dry-run did not render Storage-owned authorizer copy"
[[ -f "$DASH_DRY_PREFIX/etc/systemd/system/storage-monitor-release-puller.service" ]] || fail "dashboard dry-run did not render puller service"
[[ -f "$DASH_DRY_PREFIX/etc/systemd/system/storage-monitor-release-puller.timer" ]] || fail "dashboard dry-run did not render puller timer"
[[ -f "$DASH_DRY_PREFIX/etc/systemd/system/storage-viz-proxy.service" ]] || fail "dashboard dry-run did not render managed proxy service"
[[ -d "$DASH_DRY_PREFIX/srv/storage-viz-dashboard/releases" ]] || fail "dashboard dry-run did not render release directory"
[[ -d "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard/puller" ]] || fail "dashboard dry-run did not render puller state directory"
[[ -d "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard/legacy-proxy" ]] || fail "dashboard dry-run did not render managed legacy proxy recovery directory"
[[ -d "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard/builder" ]] || fail "dashboard dry-run did not render builder state directory"
[[ -d "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard/data" ]] || fail "dashboard dry-run did not render data directory"
[[ -d "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard/state" ]] || fail "dashboard dry-run did not render state directory"
[[ "$(file_mode "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard")" == "751" ]] || fail "dashboard state root mode does not permit builder traversal without listing"
for private_child in legacy-proxy puller builder data state; do
  [[ "$(file_mode "$DASH_DRY_PREFIX/var/lib/storage-viz-dashboard/$private_child")" == "750" ]] || fail "dashboard private child $private_child mode is not 0750"
done
grep -Fq 'ensure dir: /var/lib/storage-viz-dashboard owner=root:storage-viz mode=0751' "$DASH_LOG" || fail "dashboard deployer does not render traversable non-listable state root"
grep -Fq 'ensure dir: /var/lib/storage-viz-dashboard/builder owner=storage-viz-builder:storage-viz-builder mode=0750' "$DASH_LOG" || fail "dashboard deployer does not keep builder home isolated"
grep -Fq 'ensure dir: /var/lib/storage-viz-dashboard/data owner=storage-viz:storage-viz mode=0750' "$DASH_LOG" || fail "dashboard deployer does not preserve dashboard data ownership"
grep -Fq 'ensure dir: /var/lib/storage-viz-dashboard/state owner=storage-viz:storage-viz mode=0750' "$DASH_LOG" || fail "dashboard deployer does not preserve dashboard state ownership"
[[ "$(file_mode "$DASH_DRY_PREFIX/etc/storage-viz")" == "750" ]] || fail "dashboard dry-run config dir mode is not 0750"
[[ "$(file_mode "$DASH_DRY_PREFIX/usr/local/libexec/storage-dashboard-activate.py")" == "755" ]] || fail "activator mode is not 0755"
[[ "$(file_mode "$DASH_DRY_PREFIX/usr/local/libexec/storage-release-authorizer.py")" == "755" ]] || fail "authorizer mode is not 0755"
! grep -q 'must not run in dashboard dry-run' "$DASH_LOG" || fail "dashboard dry-run invoked a forbidden service/user action"
for src in "$STORAGE_BUILDER" "$STORAGE_ACTIVATOR" "$STORAGE_HEALTH" "$STORAGE_PROXY_LAUNCHER" "$STORAGE_PULLER" "$STORAGE_AUTHORIZER"; do
  digest="$(sha256sum "$src" 2>/dev/null | awk '{print $1}')"
  if [[ -z "$digest" ]]; then digest="$(shasum -a 256 "$src" | awk '{print $1}')"; fi
  grep -Fq "$digest" "$DASH_LOG" || fail "dashboard dry-run output missing hash for $src"
done
grep -Fq 'systemd action: daemon-reload' "$DASH_LOG" || fail "dashboard dry-run output missing daemon-reload action"
grep -Fq 'systemd action: do not enable/start storage-viz-proxy.service until approved cutover' "$DASH_LOG" || fail "dashboard dry-run output missing gated proxy start action"
grep -Fq 'systemd action: enable --now storage-monitor-release-puller.timer only after approved production health' "$DASH_LOG" || fail "dashboard dry-run output missing gated timer enable action"
expected_plan_step=1
while IFS= read -r expected_action; do
  actual_action="$(sed -n "s/^cutover plan $expected_plan_step: //p" "$DASH_LOG")"
  [[ "$actual_action" == "$expected_action" ]] || fail "dashboard dry-run cutover plan step $expected_plan_step mismatch: <$actual_action>"
  expected_plan_step=$((expected_plan_step + 1))
done <<'PLAN'
prepare supplied artifact as an immutable candidate release
start candidate dashboard 127.0.0.1:18088 and direct proxy 127.0.0.1:1505
run full candidate health/session/inventory/UNKNOWN_SERVER readiness
validate exact listener owners for configured live proxy endpoint and 127.0.0.1:8088
snapshot the exact validated legacy proxy into checksum-bound managed recovery storage
stop only validated live owners and legacy dashboard service
activate the exact prepared release
start managed dashboard and proxy services
run production health through configured proxy listener with public :505 Host/Origin
enable --now storage-monitor-release-puller.timer
PLAN
pass "dashboard dry-run renders assets/paths/modes/hashes without mutation"

SECOND_PREFIX="$TMP/dashboard-second-prefix"
PATH="$FAKEBIN:$PATH" "$DASHBOARD_DEPLOYER" --dry-run --prefix "$SECOND_PREFIX" >"$TMP/dashboard-second.log"
for asset in storage-dashboard-build-release.py storage-dashboard-activate.py storage-dashboard-health-check.py storage-viz-proxy-launcher.py storage-monitor-release-puller.py storage-release-authorizer.py; do
  cmp -s "$DASH_DRY_PREFIX/usr/local/libexec/$asset" "$SECOND_PREFIX/usr/local/libexec/$asset" || fail "dashboard dry-run is not idempotent for $asset"
done
pass "dashboard deployer dry-run is idempotent"

DASH_REAL_PREFIX="$TMP/dashboard-real-prefix"
DASH_REAL_SYSTEMCTL_LOG="$TMP/dashboard-real-systemctl.log"
cat > "$FAKEBIN/dashboard-systemctl" <<'FAKE'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "${DASH_REAL_SYSTEMCTL_LOG:?}"
FAKE
chmod +x "$FAKEBIN/dashboard-systemctl"
DASH_REAL_SYSTEMCTL_LOG="$DASH_REAL_SYSTEMCTL_LOG" SYSTEMCTL="$FAKEBIN/dashboard-systemctl" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_REAL_WITH_PREFIX=1 \
  "$DASHBOARD_DEPLOYER" --prefix "$DASH_REAL_PREFIX" >"$TMP/dashboard-real-install.out"
[[ "$(cat "$DASH_REAL_SYSTEMCTL_LOG")" == "systemctl daemon-reload" ]] || fail "ordinary dashboard install performed service/timer action: $(cat "$DASH_REAL_SYSTEMCTL_LOG")"
pass "ordinary dashboard install only reloads systemd and leaves live services untouched"

DASH_CUTOVER_TMP="$TMP/dashboard-cutover"
mkdir -p "$DASH_CUTOVER_TMP/bin" "$DASH_CUTOVER_TMP/incoming" "$DASH_CUTOVER_TMP/proc/5050" "$DASH_CUTOVER_TMP/proc/8088"
DASH_ARTIFACT="$DASH_CUTOVER_TMP/incoming/storage-monitor-dashboard-1111111111111111111111111111111111111111.tar.gz"
DASH_METADATA="$DASH_CUTOVER_TMP/incoming/storage-monitor-dashboard-1111111111111111111111111111111111111111.sha256.json"
DASH_CANDIDATE_ROOT="$DASH_CUTOVER_TMP/prepared/1111111111111111111111111111111111111111/storage-monitor"
mkdir -p "$DASH_CANDIDATE_ROOT/viewer" "$DASH_CANDIDATE_ROOT/deploy"
printf 'artifact' > "$DASH_ARTIFACT"
printf '{"application_name":"storage-monitor"}
' > "$DASH_METADATA"
DASH_DIGEST="$(sha256sum "$DASH_ARTIFACT" 2>/dev/null | awk '{print $1}')"
if [[ -z "$DASH_DIGEST" ]]; then DASH_DIGEST="$(shasum -a 256 "$DASH_ARTIFACT" | awk '{print $1}')"; fi
cat > "$DASH_CUTOVER_TMP/dashboard.env" <<FAKE
STORAGE_VIZ_BIND=127.0.0.1
STORAGE_VIZ_PORT=8088
STORAGE_VIZ_TRUSTED_PROXY=1
STORAGE_VIZ_ALLOWED_ORIGINS=http://storage.example:505
STORAGE_VIZ_OPERATOR_ALLOWLIST=ops-viewer,fixed-proxy-operator
STORAGE_VIZ_SESSION_COOKIE_SECURE=0
STORAGE_VIZ_INVENTORY=$DASH_CUTOVER_TMP/servers.json
FAKE
cat > "$DASH_CUTOVER_TMP/proxy.env" <<'FAKE'
STORAGE_VIZ_PROXY_BIND=0.0.0.0
STORAGE_VIZ_PROXY_PORT=505
STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1
STORAGE_VIZ_PROXY_UPSTREAM_PORT=8088
STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator
STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://storage.example:505
FAKE
printf '{"servers":[{"id":"atlas","enabled":true}]}' > "$DASH_CUTOVER_TMP/servers.json"
cat > "$DASH_CANDIDATE_ROOT/viewer/serve.py" <<'FAKE'
import json, os, signal, sys, time
keys = ["PYTHONDONTWRITEBYTECODE", "STORAGE_VIZ_ROOT", "STORAGE_VIZ_BIND", "STORAGE_VIZ_PORT", "STORAGE_VIZ_INVENTORY", "STORAGE_VIZ_DATA_DIR", "STORAGE_VIZ_STATE_DIR", "STORAGE_VIZ_PREFLIGHT_BACKEND", "STORAGE_VIZ_TRUSTED_PROXY", "STORAGE_VIZ_ALLOWED_ORIGINS", "STORAGE_VIZ_OPERATOR_ALLOWLIST", "STORAGE_VIZ_SESSION_COOKIE_SECURE"]
with open(os.environ["DASH_CUTOVER_LOG"], "a", encoding="utf-8") as out:
    out.write("candidate-dashboard " + json.dumps({"argv": sys.argv[1:], "env": {key: os.environ.get(key) for key in keys}}, sort_keys=True) + "\n")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True: time.sleep(1)
FAKE
cat > "$DASH_CANDIDATE_ROOT/deploy/direct_proxy.py" <<'FAKE'
import json, os, signal, sys, time
keys = ["PYTHONDONTWRITEBYTECODE", "STORAGE_VIZ_PROXY_BIND", "STORAGE_VIZ_PROXY_PORT", "STORAGE_VIZ_PROXY_UPSTREAM_HOST", "STORAGE_VIZ_PROXY_UPSTREAM_PORT", "STORAGE_VIZ_PROXY_OPERATOR", "STORAGE_VIZ_PROXY_PUBLIC_ORIGIN"]
with open(os.environ["DASH_CUTOVER_LOG"], "a", encoding="utf-8") as out:
    out.write("candidate-proxy " + json.dumps({"argv": sys.argv[1:], "env": {key: os.environ.get(key) for key in keys}}, sort_keys=True) + "\n")
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
while True: time.sleep(1)
FAKE
cat > "$DASH_CUTOVER_TMP/bin/ss" <<'FAKE'
#!/usr/bin/env bash
printf 'ss %s
' "$*" >> "${DASH_CUTOVER_LOG:?}"
case "$*" in
  *:505*) printf 'LISTEN 0 4096 0.0.0.0:505 0.0.0.0:* users:(("python3.12",pid=5050,fd=3))
' ;;
  *:8088*) printf 'LISTEN 0 4096 192.168.0.3:8088 0.0.0.0:* users:(("python3.12",pid=5050,fd=3))
LISTEN 0 4096 127.0.0.1:8088 0.0.0.0:* users:(("python3.12",pid=8088,fd=3))
' ;;
esac
FAKE
cat > "$DASH_CUTOVER_TMP/bin/systemctl" <<'FAKE'
#!/usr/bin/env bash
printf 'systemctl %s
' "$*" >> "${DASH_CUTOVER_LOG:?}"
if [[ "$*" == "show -p MainPID --value storage-viz-dashboard.service" ]]; then printf '8088\n'; exit 0; fi
if [[ "$*" == "start storage-viz-dashboard.service storage-viz-proxy.service" && "${SYSTEMCTL_START_RC_ONCE:-0}" != 0 ]]; then
  count=0
  [[ -f "${SYSTEMCTL_START_COUNT_FILE:?}" ]] && count="$(cat "$SYSTEMCTL_START_COUNT_FILE")"
  count=$((count + 1))
  printf '%s' "$count" > "$SYSTEMCTL_START_COUNT_FILE"
  [[ "$count" == 1 ]] && exit "$SYSTEMCTL_START_RC_ONCE"
fi
exit 0
FAKE
cat > "$DASH_CUTOVER_TMP/bin/python3.12" <<'FAKE'
#!/usr/bin/env bash
printf 'python3.12 %s
' "$*" >> "${DASH_CUTOVER_LOG:?}"
exec "${REAL_PYTHON:?}" "$@"
FAKE
cat > "$DASH_CUTOVER_TMP/bin/activate" <<'FAKE'
#!/usr/bin/env bash
printf 'activate %s
' "$*" >> "${DASH_CUTOVER_LOG:?}"
if [[ "$*" == *--rollback-state* ]]; then
  if [[ "${ROLLBACK_ERROR_KIND:-}" == unsafe ]]; then
    printf '{"status":"error","error":"managed legacy proxy digest mismatch"}\n' >&2
    exit 1
  fi
  if [[ "${ROLLBACK_ERROR_KIND:-}" == malformed ]]; then
    printf '{"status":"error","error":"activation state is invalid or unreadable for rollback"}\n' >&2
    exit 1
  fi
  if [[ "${ACTIVATE_FAIL_BEFORE_STATE:-0}" == 1 ]]; then
    printf '{"status":"error","error":"activation state is unavailable for rollback"}\n' >&2
    exit 1
  fi
  printf '{"status":"rolled_back"}\n'
  exit 0
fi
if [[ "$*" == *--record-restored-legacy* ]]; then
  target=""; digest=""; original=""; app=""; previous=""
  for arg in "$@"; do
    [[ "$previous" == --legacy-proxy-recovery-target ]] && target="$arg"
    [[ "$previous" == --legacy-proxy-recovery-sha256 ]] && digest="$arg"
    [[ "$previous" == --legacy-proxy-original-path ]] && original="$arg"
    [[ "$previous" == --app-path ]] && app="$arg"
    previous="$arg"
  done
  app="$(cd "$app" && pwd -P)"
  printf '{"status":"rolled_back","restored":"%s","restored_legacy_target":"%s","managed_legacy_proxy_target":"%s","managed_legacy_proxy_sha256":"%s","legacy_proxy_original_path":"%s"}\n' "$app" "$app" "$target" "$digest" "$original"
  exit 0
fi
if [[ "$*" == *--prepare-legacy-proxy* ]]; then
  source=""; previous=""
  for arg in "$@"; do
    [[ "$previous" == --legacy-proxy-source ]] && source="$arg"
    previous="$arg"
  done
  source="$(cd "$(dirname "$source")" && pwd -P)/$(basename "$source")"
  digest="$(sha256sum "$source" 2>/dev/null | awk '{print $1}')"
  [[ -n "$digest" ]] || digest="$(shasum -a 256 "$source" | awk '{print $1}')"
  managed_root="$(cd "${DASH_CANDIDATE_ROOT:?}/../../.." && pwd -P)/managed-legacy"
  printf '{"status":"legacy_proxy_prepared","legacy_proxy_original_path":"%s","managed_legacy_proxy_target":"%s/direct_proxy.py","managed_legacy_proxy_sha256":"%s"}\n' "$source" "$managed_root" "$digest"
  exit 0
fi
cat >/dev/null
if [[ "$*" == *--prepare-only* ]]; then
  printf '{"status":"prepared","source_sha":"1111111111111111111111111111111111111111","archive_digest":"%s","candidate_release":"%s"}\n' "${DASH_DIGEST:?}" "${DASH_CANDIDATE_ROOT:?}"
  exit 0
fi
if [[ "${ACTIVATE_FAIL_BEFORE_STATE:-0}" == 1 ]]; then exit 42; fi
printf '{"status":"active","source_sha":"1111111111111111111111111111111111111111","archive_digest":"%s","release":"%s","legacy_backup":"/opt/storage-viz-dashboard.legacy"}\n' "${DASH_DIGEST:?}" "${DASH_CANDIDATE_ROOT:?}"
exit 0
FAKE
cat > "$DASH_CUTOVER_TMP/bin/health-check.py" <<'FAKE'
#!/usr/bin/env python3.12
import os, sys
with open(os.environ["DASH_CUTOVER_LOG"], "a", encoding="utf-8") as out:
    mode = "candidate-health" if "--skip-service-check" in sys.argv else "production-health"
    out.write(mode + " " + " ".join(sys.argv[1:]) + "\n")
FAKE
cat > "$DASH_CUTOVER_TMP/bin/kill" <<'FAKE'
#!/usr/bin/env bash
printf 'kill %s
' "$*" >> "${DASH_CUTOVER_LOG:?}"
if [[ "$#" == 2 && -d "${PROC_ROOT:?}/$2" ]]; then exit 0; fi
/bin/kill "$@" 2>/dev/null || true
exit 0
FAKE
chmod +x "$DASH_CUTOVER_TMP/bin"/*
mkdir -p "$DASH_CUTOVER_TMP/storage-viz-direct"
DASH_LEGACY_PROXY="$DASH_CUTOVER_TMP/storage-viz-direct/proxy.py"
printf '#!/usr/bin/env python3.12\n' > "$DASH_LEGACY_PROXY"
chmod 0755 "$DASH_LEGACY_PROXY"
export LEGACY_PROXY_PATH="$DASH_LEGACY_PROXY"
DASH_LIVE_APP="$DASH_CUTOVER_TMP/live-opt"
mkdir -p "$DASH_LIVE_APP/viewer"
printf '#!/usr/bin/env python3.12\n' > "$DASH_LIVE_APP/viewer/serve.py"
chmod 0755 "$DASH_LIVE_APP/viewer/serve.py"
export APP_PATH="$DASH_LIVE_APP"
export LEGACY_DASHBOARD_PATH="$DASH_LIVE_APP/viewer/serve.py"
ln -s /usr/bin/python3.12 "$DASH_CUTOVER_TMP/proc/5050/exe"
ln -s /usr/bin/python3.12 "$DASH_CUTOVER_TMP/proc/8088/exe"
printf 'python3.12\0%s\0' "$DASH_LEGACY_PROXY" > "$DASH_CUTOVER_TMP/proc/5050/cmdline"
printf '0::/user.slice/legacy-proxy.scope\n' > "$DASH_CUTOVER_TMP/proc/5050/cgroup"
printf '1000.00 0.00\n' > "$DASH_CUTOVER_TMP/proc/uptime"
CLK_TCK="$(getconf CLK_TCK)"
printf '5050 (python3.12) S%s %s 0 0 0 0 0 0 0 0\n' "$(printf ' 0%.0s' {1..18})" "$((1000 * CLK_TCK))" > "$DASH_CUTOVER_TMP/proc/5050/stat"
printf 'python3.12\0%s\0' "$DASH_LIVE_APP/viewer/serve.py" > "$DASH_CUTOVER_TMP/proc/8088/cmdline"
printf '0::/system.slice/storage-viz-dashboard.service\n' > "$DASH_CUTOVER_TMP/proc/8088/cgroup"
DASH_CUTOVER_LOG="$DASH_CUTOVER_TMP/cutover.log"
export DASH_CUTOVER_LOG
DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/cutover.out"
line_prepare=$(grep -n '^activate --prepare-only' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_candidate=$(grep -n '^candidate-dashboard ' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_proxy=$(grep -n '^candidate-proxy ' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_probe=$(grep -n '^candidate-health ' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_owner=$(grep -n '^ss .*:505' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_legacy_snapshot=$(grep -n '^activate --prepare-legacy-proxy' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_stop=$(grep -n '^kill .*5050' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_activate=$(grep -n '^activate --sha' "$DASH_CUTOVER_LOG" | tail -1 | cut -d: -f1)
line_start=$(grep -n '^systemctl start storage-viz-dashboard.service storage-viz-proxy.service' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
line_health=$(grep -n '^production-health ' "$DASH_CUTOVER_LOG" | tail -1 | cut -d: -f1)
line_timer=$(grep -n '^systemctl enable --now storage-monitor-release-puller.timer' "$DASH_CUTOVER_LOG" | head -1 | cut -d: -f1)
[[ -n "$line_prepare" && -n "$line_candidate" && -n "$line_proxy" && -n "$line_probe" && -n "$line_owner" && -n "$line_legacy_snapshot" && -n "$line_stop" && -n "$line_activate" && -n "$line_start" && -n "$line_health" && -n "$line_timer" ]] || fail "cutover log missing required action: $(cat "$DASH_CUTOVER_LOG")"
[[ "$line_prepare" -lt "$line_candidate" && "$line_prepare" -lt "$line_proxy" && "$line_candidate" -lt "$line_probe" && "$line_proxy" -lt "$line_probe" && "$line_probe" -lt "$line_owner" && "$line_owner" -lt "$line_legacy_snapshot" && "$line_legacy_snapshot" -lt "$line_stop" && "$line_stop" -lt "$line_activate" && "$line_activate" -lt "$line_start" && "$line_start" -lt "$line_health" && "$line_health" -lt "$line_timer" ]] || fail "cutover actions out of order: $(cat "$DASH_CUTOVER_LOG")"
grep -Fq -- 'legacy proxy source verified against running process pid=5050' "$DASH_CUTOVER_TMP/cutover.out" || fail "cutover did not verify snapshot bytes against running proxy start time"
grep -Fq -- "\"STORAGE_VIZ_ROOT\": \"$DASH_CANDIDATE_ROOT\"" "$DASH_CUTOVER_LOG" || fail "candidate dashboard did not use prepared release root"
grep -Fq -- '"argv": []' "$DASH_CUTOVER_LOG" || fail "candidate scripts received unsupported argv"
! grep -Eq '^candidate-(dashboard|proxy).*"argv": \[[^]]' "$DASH_CUTOVER_LOG" || fail "candidate script received unsupported argv"
grep -Fq -- '"STORAGE_VIZ_PORT": "18088"' "$DASH_CUTOVER_LOG" || fail "candidate dashboard did not bind 18088 through serve.py env"
grep -Fq -- '"STORAGE_VIZ_PREFLIGHT_BACKEND": "1"' "$DASH_CUTOVER_LOG" || fail "candidate dashboard did not enable real preflight backend env"
grep -Fq -- '"STORAGE_VIZ_PROXY_PORT": "1505"' "$DASH_CUTOVER_LOG" || fail "candidate proxy did not bind 1505 through direct_proxy env"
[[ "$(grep -c -- '"PYTHONDONTWRITEBYTECODE": "1"' "$DASH_CUTOVER_LOG")" -ge 2 ]] || fail "candidate topology may mutate prepared release with Python bytecode"
grep -Fq -- '"STORAGE_VIZ_PROXY_PUBLIC_ORIGIN": "http://storage.example:505"' "$DASH_CUTOVER_LOG" || fail "candidate proxy lost real public origin semantics"
grep -Fq -- '--connect-host 127.0.0.1 --connect-port 1505 --skip-service-check' "$DASH_CUTOVER_LOG" || fail "candidate probe did not use real health checker candidate override"
! grep -Fq -- 'storage-viz-proxy-launcher.py' "$DASH_CUTOVER_LOG" || fail "candidate proxy incorrectly used production launcher"
grep -Fq -- '--artifact-stdin' "$DASH_CUTOVER_LOG" || fail "cutover activator did not use option-style artifact stdin"
grep -Fq -- '--legacy-proxy-recovery-target' "$DASH_CUTOVER_LOG" || fail "cutover activation did not carry managed legacy proxy recovery metadata"
! grep -Eq '^activate (upload|status|activate)( |$)' "$DASH_CUTOVER_LOG" || fail "cutover used legacy activator subcommands"
pass "dashboard bootstrap cutover runs candidate probes, exact owner stops, option-style activation, managed start, health, then timer"

printf '5050 (python3.12) S%s %s 0 0 0 0 0 0 0 0\n' "$(printf ' 0%.0s' {1..18})" "$((800 * CLK_TCK))" > "$DASH_CUTOVER_TMP/proc/5050/stat"
if DASH_CUTOVER_LOG="$DASH_CUTOVER_TMP/changed-source.log" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/changed-source.out" 2>"$DASH_CUTOVER_TMP/changed-source.err"; then
  fail "cutover accepted a legacy proxy source changed after the running process started"
fi
grep -Fq -- 'legacy proxy source changed after running process start' "$DASH_CUTOVER_TMP/changed-source.err" || fail "changed legacy proxy source rejection was not explicit"
! grep -Eq '^kill -TERM (5050|8088)$' "$DASH_CUTOVER_TMP/changed-source.log" || fail "changed legacy proxy source rejection stopped a live owner"
printf '5050 (python3.12) S%s %s 0 0 0 0 0 0 0 0\n' "$(printf ' 0%.0s' {1..18})" "$((1000 * CLK_TCK))" > "$DASH_CUTOVER_TMP/proc/5050/stat"
pass "dashboard cutover snapshots only proxy source bytes unchanged since process start"

cat > "$DASH_CUTOVER_TMP/proxy.env" <<'FAKE'
STORAGE_VIZ_PROXY_BIND=192.168.0.3
STORAGE_VIZ_PROXY_PORT=8088
STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1
STORAGE_VIZ_PROXY_UPSTREAM_PORT=8088
STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator
STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://storage.example:505
FAKE
DASH_NAT_CUTOVER_LOG="$DASH_CUTOVER_TMP/nat-cutover.log"
DASH_CUTOVER_LOG="$DASH_NAT_CUTOVER_LOG" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/nat-cutover.out"
grep -Fq -- 'ss -H -ltnp sport = :8088' "$DASH_NAT_CUTOVER_LOG" || fail "NAT cutover did not inspect shared numeric port"
grep -Eq '^kill -TERM 5050$' "$DASH_NAT_CUTOVER_LOG" || fail "NAT cutover did not stop exact proxy owner"
grep -Eq '^kill -TERM 8088$' "$DASH_NAT_CUTOVER_LOG" || fail "NAT cutover did not stop exact dashboard owner"
grep -Fq -- 'systemctl enable --now storage-monitor-release-puller.timer' "$DASH_NAT_CUTOVER_LOG" || fail "NAT cutover did not enable five-minute puller"
pass "dashboard bootstrap cutover supports NAT-mapped proxy listener sharing dashboard numeric port on another address"

cat > "$DASH_CUTOVER_TMP/proxy-conflict.env" <<'FAKE'
STORAGE_VIZ_PROXY_BIND=0.0.0.0
STORAGE_VIZ_PROXY_PORT=8088
STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1
STORAGE_VIZ_PROXY_UPSTREAM_PORT=8088
STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator
STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://storage.example:505
FAKE
if DASH_CUTOVER_LOG="$DASH_CUTOVER_TMP/conflict.log" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy-conflict.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/conflict.out" 2>"$DASH_CUTOVER_TMP/conflict.err"; then
  fail "cutover accepted wildcard proxy on dashboard port"
fi
grep -Fq -- 'wildcard proxy cannot share dashboard port 8088' "$DASH_CUTOVER_TMP/conflict.err" || fail "cutover did not reject wildcard/dashboard port conflict explicitly"
[[ ! -f "$DASH_CUTOVER_TMP/conflict.log" ]] || ! grep -Eq '^kill -TERM ' "$DASH_CUTOVER_TMP/conflict.log" || fail "conflicting cutover stopped a live owner"
pass "dashboard bootstrap rejects wildcard proxy sharing dashboard port"

DASH_ROLLBACK_LOG="$DASH_CUTOVER_TMP/rollback.log"
if DASH_CUTOVER_LOG="$DASH_ROLLBACK_LOG" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" SYSTEMCTL_START_RC_ONCE=42 SYSTEMCTL_START_COUNT_FILE="$DASH_CUTOVER_TMP/start-count" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/rollback.out" 2>"$DASH_CUTOVER_TMP/rollback.err"; then
  fail "cutover succeeded after managed service start failure"
fi
grep -Fq -- 'activate --rollback-state' "$DASH_ROLLBACK_LOG" || fail "rollback did not invoke activator rollback contract"
[[ "$(grep -c '^systemctl start storage-viz-dashboard.service storage-viz-proxy.service' "$DASH_ROLLBACK_LOG")" -ge 2 ]] || fail "rollback did not restart managed dashboard and proxy against restored legacy"
grep -Fq -- 'production-health ' "$DASH_ROLLBACK_LOG" || fail "rollback did not require previous inventory health on 505"
grep -Eq '^kill -TERM 5050$' "$DASH_ROLLBACK_LOG" || fail "rollback/cutover did not use exact PID kill only"
! grep -Fq -- 'systemctl enable --now storage-monitor-release-puller.timer' "$DASH_ROLLBACK_LOG" || fail "failed cutover enabled puller timer"
! grep -Eq 'pkill|killall|fuser -k|tmux new|new-session' "$DASH_ROLLBACK_LOG" || fail "rollback used broad kill or recreated tmux"
pass "dashboard cutover failure invokes rollback contract and validates previous 505 health without broad kill/tmux recreate"

DASH_UNSAFE_ROLLBACK_LOG="$DASH_CUTOVER_TMP/unsafe-rollback.log"
rm -f "$DASH_CUTOVER_TMP/start-count"
if DASH_CUTOVER_LOG="$DASH_UNSAFE_ROLLBACK_LOG" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" SYSTEMCTL_START_RC_ONCE=42 SYSTEMCTL_START_COUNT_FILE="$DASH_CUTOVER_TMP/start-count" ROLLBACK_ERROR_KIND=unsafe STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/unsafe-rollback.out" 2>"$DASH_CUTOVER_TMP/unsafe-rollback.err"; then
  fail "cutover accepted unsafe rollback-state validation failure"
fi
grep -Fq -- 'managed legacy proxy digest mismatch' "$DASH_CUTOVER_TMP/unsafe-rollback.err" || fail "unsafe rollback error was hidden"
! grep -Fq -- 'activate --record-restored-legacy' "$DASH_UNSAFE_ROLLBACK_LOG" || fail "unsafe rollback error was overwritten by legacy record fallback"
[[ "$(grep -c '^systemctl start storage-viz-dashboard.service storage-viz-proxy.service' "$DASH_UNSAFE_ROLLBACK_LOG")" == 1 ]] || fail "unsafe rollback restarted services after validation failure"
! grep -Fq -- 'production-health ' "$DASH_UNSAFE_ROLLBACK_LOG" || fail "unsafe rollback ran health after validation failure"
pass "dashboard rollback fails closed on path or digest validation errors"

DASH_MALFORMED_ROLLBACK_LOG="$DASH_CUTOVER_TMP/malformed-rollback.log"
rm -f "$DASH_CUTOVER_TMP/start-count"
if DASH_CUTOVER_LOG="$DASH_MALFORMED_ROLLBACK_LOG" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" SYSTEMCTL_START_RC_ONCE=42 SYSTEMCTL_START_COUNT_FILE="$DASH_CUTOVER_TMP/start-count" ROLLBACK_ERROR_KIND=malformed STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/malformed-rollback.out" 2>"$DASH_CUTOVER_TMP/malformed-rollback.err"; then
  fail "cutover accepted malformed rollback state"
fi
grep -Fq -- 'activation state is invalid or unreadable for rollback' "$DASH_CUTOVER_TMP/malformed-rollback.err" || fail "malformed rollback-state error was hidden"
! grep -Fq -- 'activate --record-restored-legacy' "$DASH_MALFORMED_ROLLBACK_LOG" || fail "malformed rollback state entered no-state recovery"
[[ "$(grep -c '^systemctl start storage-viz-dashboard.service storage-viz-proxy.service' "$DASH_MALFORMED_ROLLBACK_LOG")" == 1 ]] || fail "malformed rollback state restarted services"
! grep -Fq -- 'production-health ' "$DASH_MALFORMED_ROLLBACK_LOG" || fail "malformed rollback state ran production health"
pass "dashboard rollback distinguishes absent state from malformed state"

DASH_RECOVERY_LOG="$DASH_CUTOVER_TMP/recovery.log"
DASH_RECOVERY_APP="$DASH_CUTOVER_TMP/legacy-opt"
DASH_RECOVERY_PROC="$DASH_CUTOVER_TMP/recovery-proc"
mkdir -p "$DASH_RECOVERY_APP/viewer" "$DASH_RECOVERY_APP/deploy" "$DASH_RECOVERY_PROC/5050" "$DASH_RECOVERY_PROC/8088"
DASH_RECOVERY_APP="$(cd "$DASH_RECOVERY_APP" && pwd -P)"
printf '#!/usr/bin/env python3.12\n' > "$DASH_RECOVERY_APP/viewer/serve.py"
printf '#!/usr/bin/env python3.12\n' > "$DASH_RECOVERY_APP/deploy/direct_proxy.py"
chmod 0755 "$DASH_RECOVERY_APP/viewer/serve.py" "$DASH_RECOVERY_APP/deploy/direct_proxy.py"
ln -s /usr/bin/python3.12 "$DASH_RECOVERY_PROC/5050/exe"
ln -s /usr/bin/python3.12 "$DASH_RECOVERY_PROC/8088/exe"
printf 'python3.12\0%s\0' "$DASH_RECOVERY_APP/deploy/direct_proxy.py" > "$DASH_RECOVERY_PROC/5050/cmdline"
printf '0::/user.slice/legacy-proxy.scope\n' > "$DASH_RECOVERY_PROC/5050/cgroup"
printf '1000.00 0.00\n' > "$DASH_RECOVERY_PROC/uptime"
printf '5050 (python3.12) S%s %s 0 0 0 0 0 0 0 0\n' "$(printf ' 0%.0s' {1..18})" "$((1000 * CLK_TCK))" > "$DASH_RECOVERY_PROC/5050/stat"
printf 'python3.12\0%s\0' "$DASH_RECOVERY_APP/viewer/serve.py" > "$DASH_RECOVERY_PROC/8088/cmdline"
printf '0::/system.slice/storage-viz-dashboard.service\n' > "$DASH_RECOVERY_PROC/8088/cgroup"
if DASH_CUTOVER_LOG="$DASH_RECOVERY_LOG" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_CUTOVER_TMP/bin/ss" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_RECOVERY_PROC" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STATE_ROOT="$DASH_CUTOVER_TMP/recovery-state" APP_PATH="$DASH_RECOVERY_APP" LEGACY_PROXY_PATH="$DASH_RECOVERY_APP/deploy/direct_proxy.py" LEGACY_DASHBOARD_PATH="$DASH_RECOVERY_APP/viewer/serve.py" ACTIVATE_FAIL_BEFORE_STATE=1 STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/recovery.out" 2>"$DASH_CUTOVER_TMP/recovery.err"; then
  fail "cutover unexpectedly succeeded after pre-state activation failure"
fi
line_failed_activate=$(grep -n '^activate --sha' "$DASH_RECOVERY_LOG" | tail -1 | cut -d: -f1 || true)
line_no_state=$(grep -n '^activate --rollback-state' "$DASH_RECOVERY_LOG" | tail -1 | cut -d: -f1 || true)
line_recovery=$(grep -n '^activate --record-restored-legacy' "$DASH_RECOVERY_LOG" | tail -1 | cut -d: -f1 || true)
line_recovery_start=$(grep -n '^systemctl start storage-viz-dashboard.service storage-viz-proxy.service' "$DASH_RECOVERY_LOG" | tail -1 | cut -d: -f1 || true)
line_recovery_health=$(grep -n '^production-health ' "$DASH_RECOVERY_LOG" | tail -1 | cut -d: -f1 || true)
[[ -n "$line_failed_activate" && -n "$line_no_state" && -n "$line_recovery" && -n "$line_recovery_start" && -n "$line_recovery_health" ]] || fail "no-state recovery log missing required action: $(cat "$DASH_RECOVERY_LOG")"
[[ "$line_failed_activate" -lt "$line_no_state" && "$line_no_state" -lt "$line_recovery" && "$line_recovery" -lt "$line_recovery_start" && "$line_recovery_start" -lt "$line_recovery_health" ]] || fail "no-state recovery actions out of order: $(cat "$DASH_RECOVERY_LOG")"
! grep -Fq -- 'systemctl enable --now storage-monitor-release-puller.timer' "$DASH_RECOVERY_LOG" || fail "failed no-state recovery enabled puller timer"
pass "pre-state activation failure records restored legacy state then restarts both managed services and checks previous inventory health"

DASH_BAD_SS="$DASH_CUTOVER_TMP/bin/ss-bad"
cat > "$DASH_BAD_SS" <<'FAKE'
#!/usr/bin/env bash
printf 'ss %s
' "$*" >> "${DASH_CUTOVER_LOG:?}"
printf 'LISTEN 0 4096 192.168.0.3:8088 0.0.0.0:* users:(("unrelated",pid=9999,fd=3))
'
FAKE
chmod +x "$DASH_BAD_SS"
if DASH_CUTOVER_LOG="$DASH_CUTOVER_TMP/bad-owner.log" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_BAD_SS" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/bad-owner.out" 2>"$DASH_CUTOVER_TMP/bad-owner.err"; then
  fail "cutover accepted unrelated port owner"
fi
! grep -Eq '^kill -TERM (9999|5050)$' "$DASH_CUTOVER_TMP/bad-owner.log" || fail "cutover killed a live owner after unrelated owner rejection"
pass "dashboard cutover rejects unrelated listener owners before stopping anything"

DASH_MULTI_SS="$DASH_CUTOVER_TMP/bin/ss-multi"
cat > "$DASH_MULTI_SS" <<'FAKE'
#!/usr/bin/env bash
printf 'ss %s\n' "$*" >> "${DASH_CUTOVER_LOG:?}"
printf 'LISTEN 0 4096 192.168.0.3:8088 0.0.0.0:* users:(("python3.12",pid=5050,fd=3),("python3.12",pid=6060,fd=4))\n'
FAKE
chmod +x "$DASH_MULTI_SS"
if DASH_CUTOVER_LOG="$DASH_CUTOVER_TMP/multi-owner.log" DASH_DIGEST="$DASH_DIGEST" DASH_CANDIDATE_ROOT="$DASH_CANDIDATE_ROOT" REAL_PYTHON="$(command -v python3.12)" PATH="$DASH_CUTOVER_TMP/bin:$PATH" \
  SYSTEMCTL="$DASH_CUTOVER_TMP/bin/systemctl" SS="$DASH_MULTI_SS" PYTHON="$DASH_CUTOVER_TMP/bin/python3.12" ACTIVATOR="$DASH_CUTOVER_TMP/bin/activate" KILL="$DASH_CUTOVER_TMP/bin/kill" PROC_ROOT="$DASH_CUTOVER_TMP/proc" HEALTH_CHECKER="$DASH_CUTOVER_TMP/bin/health-check.py" DASHBOARD_ENV="$DASH_CUTOVER_TMP/dashboard.env" PROXY_ENV="$DASH_CUTOVER_TMP/proxy.env" RELEASE_ROOT="$DASH_CUTOVER_TMP/prepared" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 STORAGE_VIZ_INSTALL_TEST_ENABLE_CUTOVER=1 \
  "$DASHBOARD_DEPLOYER" --bootstrap-cutover --candidate-sha 1111111111111111111111111111111111111111 --expected-digest "$DASH_DIGEST" --artifact "$DASH_ARTIFACT" --metadata "$DASH_METADATA" >"$DASH_CUTOVER_TMP/multi-owner.out" 2>"$DASH_CUTOVER_TMP/multi-owner.err"; then
  fail "cutover accepted a listener record with multiple PID owners"
fi
! grep -Eq '^kill -TERM (5050|6060|8088)$' "$DASH_CUTOVER_TMP/multi-owner.log" || fail "cutover killed a live owner after multi-PID rejection"
pass "dashboard cutover rejects listener records with multiple pid tokens"

assert_contains "$STORAGE_PULLER_TIMER" "OnBootSec=2min"
assert_contains "$STORAGE_PULLER_TIMER" "OnCalendar=*:0/5"
assert_contains "$STORAGE_PULLER_TIMER" "Persistent=true"
assert_contains "$STORAGE_PULLER_TIMER" "RandomizedDelaySec=90s"
assert_contains "$STORAGE_PULLER_TIMER" "AccuracySec=15s"
assert_not_grep "$STORAGE_PULLER_TIMER" 'OnUnitActiveSec|OnCalendar=.*(1|2|3|4)min' "non-calendar or API-hammering cadence"
pass "storage release puller timer matches GPU five-minute cadence exactly"

assert_contains "$STORAGE_PULLER_SERVICE" "User=root"
assert_contains "$STORAGE_PULLER_SERVICE" "Group=root"
assert_contains "$STORAGE_PULLER_SERVICE" "UMask=0077"
assert_contains "$STORAGE_PULLER_SERVICE" "Nice=10"
assert_contains "$STORAGE_PULLER_SERVICE" "IOSchedulingClass=idle"
assert_contains "$STORAGE_PULLER_SERVICE" "TimeoutStartSec=30min"
assert_contains "$STORAGE_PULLER_SERVICE" "CPUWeight=20"
assert_contains "$STORAGE_PULLER_SERVICE" "IOWeight=20"
assert_contains "$STORAGE_PULLER_SERVICE" "ProtectSystem=strict"
assert_contains "$STORAGE_PULLER_SERVICE" "ProtectHome=yes"
assert_contains "$STORAGE_PULLER_SERVICE" "ReadWritePaths=/var/lib/storage-viz-dashboard /srv/storage-viz-dashboard/releases /opt"
assert_not_grep "$STORAGE_PULLER_SERVICE" '^ReadWritePaths=.* /opt/storage-viz-dashboard$' "symlink target allowance cannot replace the link in read-only /opt"
assert_contains "$STORAGE_PULLER_SERVICE" "ReadOnlyPaths=/usr/local/libexec"
assert_contains "$STORAGE_PULLER_SERVICE" "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX"
assert_grep "$STORAGE_PULLER_SERVICE" "ExecStart=/usr/bin/python3 /usr/local/libexec/storage-monitor-release-puller\.py --repository IRCVLab/GPU_monitor" "portable system Python on puller ExecStart"
assert_grep "$STORAGE_PULLER_SERVICE" ".*--repo-url https://github\.com/IRCVLab/GPU_monitor\.git" "explicit repository URL on puller ExecStart"
assert_contains "$STORAGE_PULLER_SERVICE" "LockPersonality=yes"
assert_contains "$STORAGE_PULLER_SERVICE" "SystemCallArchitectures=native"
assert_contains "$STORAGE_PULLER_SERVICE" "ProtectKernelTunables=yes"
assert_contains "$STORAGE_PULLER_SERVICE" "ProtectKernelModules=yes"
assert_contains "$STORAGE_PULLER_SERVICE" "ProtectControlGroups=yes"
assert_not_grep "$STORAGE_PULLER_SERVICE" 'gpu-monitor-|/opt/gpu-monitor|/var/lib/gpu-monitor|/etc/gpu-monitor|storage-viz-scan\.(service|timer)' "GPU runtime or remote scanner coupling"
pass "storage release puller service matches GPU root oneshot hardening with Storage paths"

RENDERED_PROXY_SERVICE="$DASH_DRY_PREFIX/etc/systemd/system/storage-viz-proxy.service"
assert_contains "$RENDERED_PROXY_SERVICE" "User=storage-viz"
assert_contains "$RENDERED_PROXY_SERVICE" "Group=storage-viz"
assert_contains "$RENDERED_PROXY_SERVICE" "ExecStart=/usr/bin/python3 /usr/local/libexec/storage-viz-proxy-launcher.py"
assert_not_grep "$RENDERED_PROXY_SERVICE" 'storage-viz-proxy-launcher.py /opt/storage-viz-dashboard/deploy/direct_proxy.py' "fixed proxy target bypasses activation-state selection"
assert_not_grep "$RENDERED_PROXY_SERVICE" '^ReadWritePaths=/var/lib/storage-viz-dashboard' "proxy write access to dashboard mutable state"
assert_contains "$RENDERED_PROXY_SERVICE" "ReadOnlyPaths=/var/lib/storage-viz-dashboard/activation-state.json"
assert_contains "$RENDERED_PROXY_SERVICE" "ReadOnlyPaths=/var/lib/storage-viz-dashboard/legacy-proxy"
assert_contains "$RENDERED_PROXY_SERVICE" "InaccessiblePaths=-/var/lib/storage-viz-dashboard/data"
assert_not_grep "$RENDERED_PROXY_SERVICE" 'gpu-monitor|storage-viz-scan|5173|8000|8100' "GPU or scanner coupling in rendered managed proxy service"
pass "managed proxy service uses Storage runtime and launcher"

for python_asset in "$STORAGE_ACTIVATOR" "$STORAGE_HEALTH" "$STORAGE_PROXY_LAUNCHER" "$STORAGE_PULLER"; do
  [[ "$(head -n 1 "$python_asset")" == '#!/usr/bin/env python3' ]] || fail "$python_asset requires a version-specific Python unavailable on supported Live hosts"
done
assert_not_grep "$DASHBOARD_DEPLOYER" 'PYTHON="\$\{PYTHON:-/usr/bin/python3\.12\}"|ExecStart=/usr/bin/python3\.12' "version-specific Python runtime in dashboard deployer"
pass "dashboard deployer runtime supports the host system Python 3.10+ contract"

assert_not_grep "$DASHBOARD_DEPLOYER" 'pkill|killall|fuser -k|lsof -ti.*xargs kill' "broad process killing"
pass "dashboard deployer avoids broad process killing"


# Task 11 central dashboard/service and operations documentation contracts.
FORBIDDEN_HOST_PRODUCT_RE="$(printf '%s' 'monitoring' '_v2')|166\.104\.167\.11|$(printf '%s' '/home/ircv/workspace/' 'monitoring')|$(printf '%s' 'GPU[ _-]?' 'Monitor')|$(printf '%s' 'gpu[_-]?' 'monitor')"
CENTRAL_SERVICE="$DEPLOY/systemd/storage-viz-dashboard.service.in"
assert_file "$CENTRAL_SERVICE"
assert_contains "$CENTRAL_SERVICE" "User=storage-viz"
assert_contains "$CENTRAL_SERVICE" "Group=storage-viz"
assert_contains "$CENTRAL_SERVICE" "WorkingDirectory=/opt/storage-viz-dashboard"
assert_contains "$CENTRAL_SERVICE" "Environment=STORAGE_VIZ_BIND=127.0.0.1"
assert_contains "$CENTRAL_SERVICE" "Environment=STORAGE_VIZ_PORT=8088"
assert_contains "$CENTRAL_SERVICE" "Environment=STORAGE_VIZ_INVENTORY=/etc/storage-viz/servers.json"
assert_contains "$CENTRAL_SERVICE" "Environment=STORAGE_VIZ_DATA_DIR=/var/lib/storage-viz-dashboard/data"
assert_contains "$CENTRAL_SERVICE" "Environment=STORAGE_VIZ_STATE_DIR=/var/lib/storage-viz-dashboard/state"
assert_contains "$CENTRAL_SERVICE" "Environment=STORAGE_VIZ_TRUSTED_PROXY=1"
assert_contains "$CENTRAL_SERVICE" "EnvironmentFile=-/etc/storage-viz/dashboard.env"
assert_contains "$CENTRAL_SERVICE" "ExecStart=/usr/bin/python3 /opt/storage-viz-dashboard/viewer/serve.py"
assert_contains "$CENTRAL_SERVICE" "Restart=on-failure"
assert_contains "$CENTRAL_SERVICE" "NoNewPrivileges=yes"
assert_contains "$CENTRAL_SERVICE" "ProtectSystem=strict"
assert_contains "$CENTRAL_SERVICE" "ProtectHome=yes"
assert_contains "$CENTRAL_SERVICE" "ReadWritePaths=/var/lib/storage-viz-dashboard/data /var/lib/storage-viz-dashboard/state"
assert_not_grep "$CENTRAL_SERVICE" "storage-viz-scan|$FORBIDDEN_HOST_PRODUCT_RE" "agent/product coupling"
pass "central dashboard service has loopback/separate-path systemd contract"

assert_grep "$ROOT/install.sh" 'storage-viz-dashboard\.service' "central dashboard service installation"
assert_grep "$ROOT/install.sh" 'STORAGE_VIZ_BIND.*127\.0\.0\.1|DASHBOARD_BIND.*127\.0\.0\.1' "loopback default bind"
assert_grep "$ROOT/install.sh" 'STORAGE_VIZ_PORT.*8088|DASHBOARD_PORT.*8088' "dashboard port default"
assert_grep "$ROOT/install.sh" '/opt/storage-viz-dashboard' "separate central install root"
assert_grep "$ROOT/install.sh" '/var/lib/storage-viz-dashboard' "separate central state/data path"
assert_grep "$ROOT/install.sh" '/etc/storage-viz/servers\.json' "central inventory path"
assert_grep "$ROOT/install.sh" '/etc/storage-viz/keys' "external identity directory"
assert_grep "$ROOT/install.sh" '/etc/storage-viz/known_hosts' "external known-hosts path"
assert_not_grep "$ROOT/install.sh" "storage-viz-scan\.service|storage-viz-scan\.timer|SCAN_TARGETS|hstscan|systemctl[[:space:]]+start|systemctl[[:space:]]+enable --now.*scan|$FORBIDDEN_HOST_PRODUCT_RE" "agent install/product coupling"
pass "central installer uses separate dashboard assets and does not touch agent runtime"

DOC_FILES=("$ROOT/README.md" "$ROOT/docs/architecture.md" "$ROOT/docs/operations.md" "$ROOT/docs/host-manifest.md")
for doc in "${DOC_FILES[@]}"; do
  assert_not_grep "$doc" "$FORBIDDEN_HOST_PRODUCT_RE|password[[:space:]]*[:=][[:space:]]*[^[:space:]<]+" "forbidden host/product/password value reference"
done
assert_grep "$ROOT/docs/operations.md" '127\.0\.0\.1' "loopback dashboard default"
assert_grep "$ROOT/docs/operations.md" '8088' "dashboard port"
assert_grep "$ROOT/docs/operations.md" 'storage-viz-dashboard\.service' "central service name"
assert_grep "$ROOT/docs/operations.md" 'storage-viz-scan\.service' "agent scan service name"
assert_grep "$ROOT/docs/operations.md" 'OnUnitActiveSec=6h|six-hour|6-hour' "six-hour collection cadence"
assert_grep "$ROOT/docs/operations.md" 'reverse proxy|trusted proxy' "reverse-proxy auth documentation"
assert_grep "$ROOT/docs/operations.md" 'STORAGE_VIZ_ALLOWED_ORIGINS' "exact-origin allowlist"
assert_grep "$ROOT/docs/operations.md" 'CSRF|X-CSRF-Token' "CSRF protection"
assert_grep "$ROOT/docs/operations.md" 'monitoring.*shchoi|shchoi.*monitoring' "monitoring/shchoi bootstrap rule"
assert_grep "$ROOT/docs/operations.md" 'identity_file|known_hosts_file|/etc/storage-viz/keys|/etc/storage-viz/known_hosts' "external identity/known-host paths"
assert_grep "$ROOT/docs/operations.md" 'copy-only|copy only' "copy-only cleanup workflow"
assert_grep "$ROOT/docs/architecture.md" 'central|multi-server|multiserver' "central multi-server architecture"
assert_grep "$ROOT/docs/host-manifest.md" 'data/hosts\.json|/api/servers|servers\.json' "central host manifest docs"
assert_grep "$ROOT/docs/operations.md" 'NFS|nfs4|CIFS|SMB|sshfs|FUSE|distributed|virtual|container' "mandatory network/virtual/container mount exclusions"
assert_grep "$ROOT/docs/architecture.md" 'inventory cannot override mount policy|cannot override mount policy' "inventory cannot override mount policy"
assert_grep "$ROOT/docs/operations.md" '0600|0640|root:storage-viz|storage-viz.*read' "SSH identity owner/group/mode contract"
assert_grep "$ROOT/viewer/serve.py" 'CentralPoller|poll_once|serve_forever' "automatic central polling startup source"

RENDER_PREFIX="$TMP/render-prefix"
RENDER_LOG="$TMP/render-systemctl.log"
cat > "$FAKEBIN/systemctl" <<'FAKE'
#!/usr/bin/env bash
printf 'systemctl %s\n' "$*" >> "${FAKE_LOG:?}"
exit 0
FAKE
chmod +x "$FAKEBIN/systemctl"
FAKE_LOG="$RENDER_LOG" PATH="$FAKEBIN:$PATH" \
  STORAGE_VIZ_DASHBOARD_ROOT=/srv/storage-viz-central \
  STORAGE_VIZ_CONFIG_DIR=/srv/storage-viz-etc \
  STORAGE_VIZ_DATA_DIR=/srv/storage-viz-data \
  STORAGE_VIZ_STATE_DIR=/srv/storage-viz-state \
  STORAGE_VIZ_BIND=127.0.0.1 \
  STORAGE_VIZ_PORT=18088 \
  "$ROOT/install.sh" --dry-run --prefix "$RENDER_PREFIX" >"$TMP/render.out"
RENDERED_UNIT="$RENDER_PREFIX/etc/systemd/system/storage-viz-dashboard.service"
assert_contains "$RENDERED_UNIT" "WorkingDirectory=/srv/storage-viz-central"
assert_contains "$RENDERED_UNIT" "Environment=STORAGE_VIZ_INVENTORY=/srv/storage-viz-etc/servers.json"
assert_contains "$RENDERED_UNIT" "Environment=STORAGE_VIZ_DATA_DIR=/srv/storage-viz-data"
assert_contains "$RENDERED_UNIT" "Environment=STORAGE_VIZ_STATE_DIR=/srv/storage-viz-state"
assert_contains "$RENDERED_UNIT" "EnvironmentFile=-/srv/storage-viz-etc/dashboard.env"
assert_contains "$RENDERED_UNIT" "ReadWritePaths=/srv/storage-viz-data /srv/storage-viz-state"
[[ "$(file_mode "$RENDER_PREFIX/srv/storage-viz-etc/keys")" == "750" ]] || fail "key directory mode is not 0750"
[[ "$(file_mode "$RENDER_PREFIX/srv/storage-viz-etc/servers.json")" == "640" ]] || fail "inventory mode is not 0640"
[[ "$(file_mode "$RENDER_PREFIX/srv/storage-viz-etc/dashboard.env")" == "640" ]] || fail "dashboard env mode is not 0640"
[[ "$(file_mode "$RENDER_PREFIX/srv/storage-viz-etc/known_hosts")" == "644" ]] || fail "known_hosts mode is not 0644"
! grep -q "^systemctl" "$RENDER_LOG" 2>/dev/null || fail "dry-run override rendering called systemctl"

BAD_RENDER_PREFIX="$TMP/bad-render-prefix"
if STORAGE_VIZ_DASHBOARD_ROOT="/srv/storage viz" "$ROOT/install.sh" --dry-run --prefix "$BAD_RENDER_PREFIX" >"$TMP/bad-render.out" 2>"$TMP/bad-render.err"; then
  fail "installer accepted unsafe whitespace in template value"
fi

REAL_PREFIX="$TMP/prefix-real-install"
FAKE_LOG="$TMP/prefix-systemctl.log" PATH="$FAKEBIN:$PATH" STORAGE_VIZ_INSTALL_TEST_ASSUME_ROOT=1 "$ROOT/install.sh" --prefix "$REAL_PREFIX" >"$TMP/prefix-real.out"
! grep -q "^systemctl" "$TMP/prefix-systemctl.log" 2>/dev/null || fail "prefixed real install called systemctl"
pass "rendered central units honor overrides, reject unsafe values, and prefixed installs are safe"

pass "central operations docs record security/runtime contracts without secrets"


pass "deploy asset tests complete"
