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

for f in "$SERVICE" "$TIMER" "$SUDOERS" "$INSTALL" "$DEPLOY_SCRIPT" "$VERIFY_LINUX"; do
  assert_file "$f"
done
pass "all Task 5 deploy assets exist"

assert_grep "$VERIFY_LINUX" 'mktemp -d /tmp/storage-viz-verify\.XXXXXX' "unique temporary Linux verification directory"
assert_grep "$VERIFY_LINUX" 'git ls-files -z' "NUL-safe tracked-files-only transfer contract"
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
trap 'rm -rf "$VERIFY_TMP"' EXIT
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
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

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
trap 'rm -rf "$TMP"; rm -f "${VIEWER_SECRET:-}" "${STALE_SCANNER:-}"' EXIT
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
trap 'rm -rf "$TMP"; rm -f "$VIEWER_SECRET"' EXIT
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
