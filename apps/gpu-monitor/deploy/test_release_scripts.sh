#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
BUILD_SCRIPT="$SOURCE_ROOT/apps/gpu-monitor/deploy/build-release.sh"

mktemp_dir() {
  local created
  created=$(mktemp -d "${TMPDIR:-/tmp}/$1.XXXXXX")
  (cd "$created" && pwd -P)
}

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
log() { printf 'ok - %s\n' "$*"; }

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi
}

sha256_check() {
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$(dirname "$1")" && sha256sum -c "$(basename "$1")")
  else
    (cd "$(dirname "$1")" && shasum -a 256 -c "$(basename "$1")")
  fi
}

assert_contains() {
  local file=$1 needle=$2
  grep -Fxq "$needle" "$file" || fail "expected artifact to contain $needle"
}

assert_not_matches() {
  local file=$1 pattern=$2
  if grep -Eq "$pattern" "$file"; then
    grep -En "$pattern" "$file" >&2 || true
    fail "artifact leaked path matching $pattern"
  fi
}

assert_mode() {
  local path=$1 expected=$2 actual
  actual=$(python3 - "$path" <<'PY'
import os, stat, sys
print(f"{stat.S_IMODE(os.stat(sys.argv[1]).st_mode):04o}")
PY
)
  [[ "$actual" == "$expected" ]] || fail "$path mode is $actual, expected $expected"
}

make_fixture_repo() {
  local fixture=$1
  mkdir -p "$fixture"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.git/' \
    --exclude 'apps/gpu-monitor/.venv/' \
    --exclude 'apps/gpu-monitor/frontend/node_modules/' \
    --exclude 'apps/gpu-monitor/frontend/.svelte-kit/' \
    --exclude 'apps/gpu-monitor/frontend/build/' \
    --exclude 'apps/gpu-monitor/backend/__pycache__/' \
    --exclude 'apps/gpu-monitor/backend/**/__pycache__/' \
    --exclude '.superpowers/sdd/task-4-report.md' \
    "$SOURCE_ROOT/" "$fixture/"
  git -C "$fixture" init -q
  git -C "$fixture" config user.email release-test@example.invalid
  git -C "$fixture" config user.name 'Release Script Test'
  git -C "$fixture" add -A
  git -C "$fixture" commit -q -m 'release script fixture'
}

run_builder() {
  local repo=$1 out=$2 sha=$3
  (cd "$repo" && apps/gpu-monitor/deploy/build-release.sh --sha "$sha" --output-dir "$out")
}

test_missing_builder_fails() {
  [[ -x "$BUILD_SCRIPT" ]] || fail "build-release.sh does not exist or is not executable"
  log "build script exists"
}

test_rejects_dirty_source() {
  local tmp repo out sha
  tmp=$(mktemp_dir gpu-release-dirty)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out="$tmp/out"
  make_fixture_repo "$repo"
  sha=$(git -C "$repo" rev-parse HEAD)
  printf 'dirty\n' >> "$repo/apps/gpu-monitor/backend/main.py"
  if run_builder "$repo" "$out" "$sha" >"$tmp/stdout" 2>"$tmp/stderr"; then
    fail "dirty source was accepted"
  fi
  grep -Eiq 'dirty|clean' "$tmp/stderr" || fail "dirty rejection did not explain clean checkout requirement"
  log "dirty source is rejected"
}

test_rejects_invalid_and_non_head_sha() {
  local tmp repo out head previous upper
  tmp=$(mktemp_dir gpu-release-sha)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out="$tmp/out"
  make_fixture_repo "$repo"
  head=$(git -C "$repo" rev-parse HEAD)
  if run_builder "$repo" "$out" "abc123" >"$tmp/invalid.out" 2>"$tmp/invalid.err"; then
    fail "short SHA was accepted"
  fi
  upper=$(printf '%s' "$head" | tr '[:lower:]' '[:upper:]')
  if run_builder "$repo" "$out" "$upper" >"$tmp/upper.out" 2>"$tmp/upper.err"; then
    fail "uppercase SHA was accepted"
  fi
  printf 'second\n' > "$repo/SECOND"
  git -C "$repo" add SECOND
  git -C "$repo" commit -q -m second
  previous="$head"
  if run_builder "$repo" "$out" "$previous" >"$tmp/nonhead.out" 2>"$tmp/nonhead.err"; then
    fail "non-HEAD SHA was accepted"
  fi
  log "invalid/non-HEAD SHA is rejected"
}


test_rejects_untracked_nonignored_sources_before_build() {
  local tmp repo out sha
  tmp=$(mktemp_dir gpu-release-untracked)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out="$tmp/out"
  make_fixture_repo "$repo"
  sha=$(git -C "$repo" rev-parse HEAD)

  printf 'UNTRACKED_BACKEND_PROBE = True\n' > "$repo/apps/gpu-monitor/backend/untracked_release_probe.py"
  if run_builder "$repo" "$out" "$sha" >"$tmp/backend.out" 2>"$tmp/backend.err"; then
    fail "untracked backend source was accepted"
  fi
  grep -Eiq 'untracked|clean' "$tmp/backend.err" || fail "untracked backend rejection did not explain clean checkout requirement"
  rm -f "$repo/apps/gpu-monitor/backend/untracked_release_probe.py"

  mkdir -p "$repo/apps/gpu-monitor/frontend/src/routes/review-probe"
  cat > "$repo/apps/gpu-monitor/frontend/src/routes/review-probe/+page.svelte" <<'SVELTE'
<h1>untracked frontend probe must not build</h1>
SVELTE
  if run_builder "$repo" "$out" "$sha" >"$tmp/frontend.out" 2>"$tmp/frontend.err"; then
    fail "untracked frontend source was accepted"
  fi
  grep -Eiq 'untracked|clean' "$tmp/frontend.err" || fail "untracked frontend rejection did not explain clean checkout requirement"
  log "untracked nonignored backend and frontend sources are rejected"
}

test_build_does_not_mutate_checkout_node_modules_or_build() {
  local tmp repo out sha before after
  tmp=$(mktemp_dir gpu-release-mutation)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out="$tmp/out"
  make_fixture_repo "$repo"
  sha=$(git -C "$repo" rev-parse HEAD)
  cat >> "$repo/.git/info/exclude" <<'EXCLUDES'
/apps/gpu-monitor/frontend/node_modules/
/apps/gpu-monitor/frontend/build/
EXCLUDES
  mkdir -p "$repo/apps/gpu-monitor/frontend/node_modules/local-sentinel" "$repo/apps/gpu-monitor/frontend/build"
  printf 'keep-node-modules\n' > "$repo/apps/gpu-monitor/frontend/node_modules/local-sentinel/sentinel.txt"
  printf 'keep-build\n' > "$repo/apps/gpu-monitor/frontend/build/sentinel.txt"
  before=$(find "$repo/apps/gpu-monitor/frontend/node_modules/local-sentinel" "$repo/apps/gpu-monitor/frontend/build" -type f -maxdepth 2 -print -exec shasum -a 256 {} \; | LC_ALL=C sort)
  run_builder "$repo" "$out" "$sha"
  after=$(find "$repo/apps/gpu-monitor/frontend/node_modules/local-sentinel" "$repo/apps/gpu-monitor/frontend/build" -type f -maxdepth 2 -print -exec shasum -a 256 {} \; | LC_ALL=C sort)
  [[ "$before" == "$after" ]] || fail "builder mutated checkout node_modules or build directory"
  log "builder does not mutate checkout node_modules or build outputs"
}

test_post_temp_output_failure_cleans_tmp_outputs() {
  local tmp repo out sha manifest_before
  tmp=$(mktemp_dir gpu-release-temp-cleanup)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out="$tmp/out"
  make_fixture_repo "$repo"
  sha=$(git -C "$repo" rev-parse HEAD)
  mkdir -p "$out"
  printf '{"preexisting":true}\n' > "$out/release-manifest.json"
  manifest_before=$(cat "$out/release-manifest.json")
  if run_builder "$repo" "$out" "$sha" >"$tmp/stdout" 2>"$tmp/stderr"; then
    fail "builder overwrote conflicting manifest instead of failing"
  fi
  [[ "$(cat "$out/release-manifest.json")" == "$manifest_before" ]] || fail "conflicting manifest was modified"
  if find "$out" -type f \( -name '*.tmp' -o -name 'gpu-monitor-*.tar.gz' -o -name 'gpu-monitor-*.sha256' \) | grep -q .; then
    find "$out" -type f >&2
    fail "post-temp failure left temporary or partial release outputs"
  fi
  log "post-temp output failure cleans temporary outputs"
}

test_build_outputs_contract() {
  local tmp repo out1 out2 sha artifact manifest list1 list2 checksum
  tmp=$(mktemp_dir gpu-release-contract)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out1="$tmp/out1"; out2="$tmp/out2"
  make_fixture_repo "$repo"
  sha=$(git -C "$repo" rev-parse HEAD)

  mkdir -p "$repo/apps/gpu-monitor/frontend/node_modules/leak" \
    "$repo/apps/gpu-monitor/.venv/leak" \
    "$repo/apps/gpu-monitor/frontend/.svelte-kit" \
    "$repo/apps/gpu-monitor/backend/__pycache__" \
    "$repo/apps/gpu-monitor/runtime-cache" \
    "$repo/apps/gpu-monitor/data"
  printf 'SECRET=do-not-package\n' > "$repo/apps/gpu-monitor/.env"
  printf 'sqlite bytes\n' > "$repo/apps/gpu-monitor/data/runtime.db"
  printf 'cached\n' > "$repo/apps/gpu-monitor/frontend/.svelte-kit/cache"
  printf 'module\n' > "$repo/apps/gpu-monitor/frontend/node_modules/leak/index.js"
  printf 'venv\n' > "$repo/apps/gpu-monitor/.venv/leak/file"
  printf 'pyc\n' > "$repo/apps/gpu-monitor/backend/__pycache__/main.pyc"

  run_builder "$repo" "$out1" "$sha"
  artifact="$out1/gpu-monitor-$sha.tar.gz"
  manifest="$out1/release-manifest.json"
  checksum="$out1/gpu-monitor-$sha.sha256"
  [[ -s "$artifact" ]] || fail "artifact missing"
  [[ -s "$checksum" ]] || fail "checksum missing"
  [[ -s "$manifest" ]] || fail "manifest missing"

  sha256_check "$checksum" >/dev/null || fail "checksum did not verify"
  expected_digest=$(sha256_file "$artifact" | awk '{print $1}')
  checksum_digest=$(awk '{print $1}' "$checksum")
  [[ "$expected_digest" == "$checksum_digest" ]] || fail "artifact digest and checksum file disagree"
  python3 - "$manifest" "$sha" "$artifact" "$expected_digest" <<'PY'
import json, re, sys
manifest, sha, artifact, expected_digest = sys.argv[1:]
data = json.load(open(manifest, encoding='utf-8'))
assert data == {
    "application": "gpu-monitor",
    "git_sha": sha,
    "artifact": artifact.rsplit('/', 1)[-1],
    "sha256": data["sha256"],
    "schema": 1,
}, data
assert re.fullmatch(r"[0-9a-f]{64}", data["sha256"]), data
assert data["sha256"] == expected_digest, data
PY

  python3 - "$artifact" <<'PY'
import tarfile, sys
with tarfile.open(sys.argv[1], "r:gz") as tar:
    for member in tar.getmembers():
        name = member.name
        assert not name.startswith("/"), name
        assert ".." not in name.split("/"), name
        assert not member.issym() and not member.islnk(), name
PY
  tar -tzf "$artifact" | LC_ALL=C sort > "$tmp/list1"
  list1="$tmp/list1"
  assert_contains "$list1" "gpu-monitor/backend/main.py"
  assert_contains "$list1" "gpu-monitor/backend/requirements.txt"
  assert_contains "$list1" "gpu-monitor/frontend/package.json"
  assert_contains "$list1" "gpu-monitor/frontend/package-lock.json"
  assert_contains "$list1" "gpu-monitor/frontend/build/index.js"
  assert_not_matches "$list1" '(^|/)(\.env|node_modules|\.venv|__pycache__|\.pytest_cache|\.svelte-kit|runtime-cache)(/|$)'
  assert_not_matches "$list1" '\.(db|sqlite|sqlite3|pyc)$'

  run_builder "$repo" "$out2" "$sha"
  tar -tzf "$out2/gpu-monitor-$sha.tar.gz" | LC_ALL=C sort > "$tmp/list2"
  list2="$tmp/list2"
  cmp -s "$list1" "$list2" || fail "two unchanged builds produced different file lists"
  cmp -s "$artifact" "$out2/gpu-monitor-$sha.tar.gz" || fail "two unchanged builds produced different artifact bytes"
  digest2=$(sha256_file "$out2/gpu-monitor-$sha.tar.gz" | awk '{print $1}')
  [[ "$expected_digest" == "$digest2" ]] || fail "two unchanged builds produced different artifact digests"
  log "release artifact contract is satisfied"
}

test_failed_build_leaves_no_partial_outputs_and_works_from_any_cwd() {
  local tmp repo out sha fakebin
  tmp=$(mktemp_dir gpu-release-partial)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  repo="$tmp/repo"; out="$tmp/out"; fakebin="$tmp/fakebin"
  make_fixture_repo "$repo"
  sha=$(git -C "$repo" rev-parse HEAD)
  mkdir -p "$fakebin"
  cat > "$fakebin/npm" <<'FAKENPM'
#!/usr/bin/env bash
if [[ "$1" == "ci" ]]; then
  printf 'fake npm ci failure\n' >&2
  exit 42
fi
exec /usr/bin/env npm "$@"
FAKENPM
  chmod +x "$fakebin/npm"
  if (cd /tmp && PATH="$fakebin:$PATH" "$repo/apps/gpu-monitor/deploy/build-release.sh" --sha "$sha" --output-dir "$out") >"$tmp/partial.out" 2>"$tmp/partial.err"; then
    fail "builder succeeded despite failed npm ci"
  fi
  if [[ -d "$out" ]] && find "$out" -type f | grep -q .; then
    find "$out" -type f >&2
    fail "failed build left partial output files"
  fi
  log "failed build leaves no partial outputs and script works from any CWD"
}

export_test_context() {
  local _declare _flag function_name variable
  while read -r _declare _flag function_name; do
    export -f "$function_name"
  done < <(declare -F)
  for variable in \
    SCRIPT_DIR SOURCE_ROOT BUILD_SCRIPT SERVER_DIR DEPLOY_COMMAND ACTIVATE_SCRIPT \
    HEALTH_SCRIPT INSTALLER_SCRIPT RESTART_BROKER DEV_SUDOERS LIVE_SUDOERS; do
    if [[ "${!variable+x}" == x ]]; then
      export "$variable"
    fi
  done
}

launch_test_session() {
  local name=$1
  exec python3 - "$name" <<'PY'
import os, sys
name = sys.argv[1]
os.setsid()
os.execve(
    "/bin/bash",
    ["/bin/bash", "-c", 'set -euo pipefail; "$1"', "gpu-monitor-test", name],
    os.environ,
)
PY
}

process_group_exists() {
  kill -0 -- "-$1" 2>/dev/null
}

terminate_test_group() {
  local group=$1
  process_group_exists "$group" || return 0
  kill -TERM -- "-$group" 2>/dev/null || true
  for _ in $(seq 1 10); do
    process_group_exists "$group" || return 0
    sleep 0.05
  done
  kill -KILL -- "-$group" 2>/dev/null || true
  for _ in $(seq 1 10); do
    process_group_exists "$group" || return 0
    sleep 0.05
  done
  ! process_group_exists "$group"
}

run_test() {
  local name=$1 timeout_seconds=${TEST_TIMEOUT_SECONDS:-120} marker timer_file timer_pid
  local watchdog pid status=0 timed_out=false
  if [[ -n "${TEST_FILTER:-}" && "$name" != *"$TEST_FILTER"* ]]; then
    return 0
  fi
  marker=$(mktemp "${TMPDIR:-/tmp}/gpu-release-test-timeout.${name}.XXXXXX")
  rm -f "$marker"
  timer_file="${marker}.timer"
  export_test_context
  launch_test_session "$name" &
  pid=$!
  (
    watchdog_timer=
    cleanup_watchdog_timer() {
      if [[ -n "$watchdog_timer" ]] && kill -0 "$watchdog_timer" 2>/dev/null; then
        kill "$watchdog_timer" 2>/dev/null || true
        wait "$watchdog_timer" 2>/dev/null || true
      fi
    }
    trap 'cleanup_watchdog_timer; exit 0' TERM INT HUP
    sleep "$timeout_seconds" &
    watchdog_timer=$!
    printf '%s\n' "$watchdog_timer" > "$timer_file"
    wait "$watchdog_timer" || exit 0
    if kill -0 "$pid" 2>/dev/null; then
      : > "$marker"
      terminate_test_group "$pid"
    fi
  ) &
  watchdog=$!
  wait "$pid" || status=$?
  if [[ -e "$marker" ]]; then
    timed_out=true
    wait "$watchdog" 2>/dev/null || true
  else
    kill "$watchdog" 2>/dev/null || true
    wait "$watchdog" 2>/dev/null || true
    terminate_test_group "$pid" ||
      fail "$name left processes alive in its isolated process group"
  fi
  if [[ -s "$timer_file" ]]; then
    timer_pid=$(<"$timer_file")
    if [[ "$timer_pid" =~ ^[0-9]+$ ]] && kill -0 "$timer_pid" 2>/dev/null; then
      kill "$timer_pid" 2>/dev/null || true
      wait "$timer_pid" 2>/dev/null || true
      rm -f "$marker" "$timer_file"
      fail "$name left its watchdog timer running"
    fi
  fi
  rm -f "$marker" "$timer_file"
  if [[ "$timed_out" == true ]]; then
    printf 'FAIL: %s timed out after %ss\n' "$name" "$timeout_seconds" >&2
    return 124
  fi
  return "$status"
}

test_watchdog_term_ignoring_descendant_fixture() {
  python3 -c '
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "w", encoding="utf-8") as marker:
    marker.write(f"{os.getpid()} {os.getpgrp()} {os.getsid(0)}\n")
    marker.flush()
while True:
    print("term-ignoring-descendant", flush=True)
    time.sleep(0.05)
' "$GPU_MONITOR_WATCHDOG_DESCENDANT_MARKER" &
  wait
}

test_watchdog_failure_status_fixture() {
  return 37
}

test_watchdog_isolates_and_kills_term_ignoring_process_group() {
  local tmp pipeline_pid descendant_pid descendant_pgid descendant_sid outer_pgid status elapsed start
  tmp=$(mktemp_dir gpu-release-watchdog-group)
  trap 'if [[ -s "$tmp/descendant.pid" ]]; then read -r descendant_pid _ < "$tmp/descendant.pid"; kill -KILL "$descendant_pid" 2>/dev/null || true; fi; rm -rf "$tmp"' RETURN
  outer_pgid=$(python3 -c 'import os; print(os.getpgrp())')

  start=$(date +%s)
  (
    set +e
    env GPU_MONITOR_WATCHDOG_FIXTURE=term \
      GPU_MONITOR_WATCHDOG_DESCENDANT_MARKER="$tmp/descendant.pid" \
      TEST_FILTER= \
      TEST_TIMEOUT_SECONDS=1 \
      bash "$SCRIPT_DIR/test_release_scripts.sh" 2>&1 | cat > "$tmp/timeout.out"
    printf '%s\n' "${PIPESTATUS[0]}" > "$tmp/timeout.status"
  ) &
  pipeline_pid=$!
  for _ in $(seq 1 100); do
    kill -0 "$pipeline_pid" 2>/dev/null || break
    sleep 0.05
  done
  if kill -0 "$pipeline_pid" 2>/dev/null; then
    read -r descendant_pid _ < "$tmp/descendant.pid" 2>/dev/null || descendant_pid=
    [[ -z "$descendant_pid" ]] || kill -KILL "$descendant_pid" 2>/dev/null || true
    kill -TERM "$pipeline_pid" 2>/dev/null || true
    wait "$pipeline_pid" 2>/dev/null || true
    fail "TERM-ignoring descendant retained stdout past the strict watchdog bound"
  fi
  wait "$pipeline_pid"
  elapsed=$(($(date +%s) - start))
  status=$(cat "$tmp/timeout.status")
  if [[ "$status" != 124 ]]; then
    cat "$tmp/timeout.out" >&2
    fail "watchdog timeout returned $status instead of 124"
  fi
  (( elapsed < 5 )) || fail "watchdog timeout took ${elapsed}s"
  read -r descendant_pid descendant_pgid descendant_sid < "$tmp/descendant.pid"
  [[ "$descendant_pgid" == "$descendant_sid" ]] ||
    fail "timed test did not run in a dedicated session/process group"
  [[ "$descendant_pgid" != "$outer_pgid" ]] ||
    fail "nested timed test reused the parent test process group"
  ! kill -0 "$descendant_pid" 2>/dev/null ||
    fail "TERM-ignoring descendant survived process-group KILL"

  set +e
  env GPU_MONITOR_WATCHDOG_FIXTURE=failure TEST_FILTER= TEST_TIMEOUT_SECONDS=5 \
    bash "$SCRIPT_DIR/test_release_scripts.sh" > "$tmp/failure.out" 2> "$tmp/failure.err"
  status=$?
  set -e
  [[ "$status" == 37 ]] || fail "watchdog masked original failure status 37 as $status"
  log "watchdog isolates each test and kills the full TERM-ignoring process group"
}

case "${GPU_MONITOR_WATCHDOG_FIXTURE:-}" in
  term)
    run_test test_watchdog_term_ignoring_descendant_fixture
    exit $?
    ;;
  failure)
    run_test test_watchdog_failure_status_fixture
    exit $?
    ;;
esac

run_test test_watchdog_isolates_and_kills_term_ignoring_process_group
run_test test_missing_builder_fails
run_test test_rejects_dirty_source
run_test test_rejects_invalid_and_non_head_sha
run_test test_rejects_untracked_nonignored_sources_before_build
run_test test_build_does_not_mutate_checkout_node_modules_or_build
run_test test_post_temp_output_failure_cleans_tmp_outputs
run_test test_build_outputs_contract
run_test test_failed_build_leaves_no_partial_outputs_and_works_from_any_cwd

SERVER_DIR="$SOURCE_ROOT/apps/gpu-monitor/deploy/server"
DEPLOY_COMMAND="$SERVER_DIR/gpu-monitor-deploy-command"
ACTIVATE_SCRIPT="$SERVER_DIR/activate-release.sh"
HEALTH_SCRIPT="$SERVER_DIR/health-check.sh"
INSTALLER_SCRIPT="$SERVER_DIR/install-deployer.sh"
RESTART_BROKER="$SERVER_DIR/gpu-monitor-restart-broker"
DEV_SUDOERS="$SERVER_DIR/sudoers/gpu-monitor-deploy-dev"
LIVE_SUDOERS="$SERVER_DIR/sudoers/gpu-monitor-deploy-live"

assert_symlink_target() {
  local link=$1 expected=$2 actual resolved base
  [[ -L "$link" ]] || fail "$link is not a symlink"
  actual=$(readlink "$link")
  if [[ "$actual" == "$expected" ]]; then return 0; fi
  base=$(cd "${link%/*}" && pwd -P)
  if [[ "$expected" == releases/* && -e "$link" ]]; then
    resolved=$(cd "$link" && pwd -P)
    [[ "$resolved" == "$base/$expected" ]] || fail "$link resolves to $resolved, expected $base/$expected (readlink $actual)"
    return 0
  fi
  fail "$link points to $actual, expected $expected"
}

make_release_artifact() {
  local out=$1 sha=$2 label=${3:-ok}
  local root="$out/root"
  mkdir -p "$root/gpu-monitor/backend" "$root/gpu-monitor/frontend/build"
  cp "$SOURCE_ROOT/apps/gpu-monitor/backend/main.py" "$root/gpu-monitor/backend/main.py"
  cp "$SOURCE_ROOT/apps/gpu-monitor/backend/slack_bridge.py" "$root/gpu-monitor/backend/slack_bridge.py"
  cp "$SOURCE_ROOT/apps/gpu-monitor/backend/requirements.txt" "$root/gpu-monitor/backend/requirements.txt"
  cp "$SOURCE_ROOT/apps/gpu-monitor/frontend/package.json" "$root/gpu-monitor/frontend/package.json"
  cp "$SOURCE_ROOT/apps/gpu-monitor/frontend/package-lock.json" "$root/gpu-monitor/frontend/package-lock.json"
  printf 'console.log("%s")\n' "$label" > "$root/gpu-monitor/frontend/build/index.js"
  COPYFILE_DISABLE=1 tar -C "$root" -czf "$out/gpu-monitor-$sha.tar.gz" gpu-monitor
  sha256_file "$out/gpu-monitor-$sha.tar.gz" | awk '{print $1}' > "$out/digest"
}

install_fake_server_commands() {
  local fakebin=$1 log_file=$2 health_mode=${3:-pass} restart_mode=${4:-pass}
  mkdir -p "$fakebin"
  printf '%s\n' "$restart_mode" > "$fakebin/restart-mode"
  printf '0\n' > "$fakebin/restart-count"
  printf '0\n' > "$fakebin/health-count"
  cat > "$fakebin/systemctl" <<FAKE
#!/usr/bin/env bash
printf 'systemctl %s\\n' "\$*" >> '$log_file'
count=\$((\$(cat '$fakebin/restart-count') + 1))
printf '%s\\n' "\$count" > '$fakebin/restart-count'
mode=\$(cat '$fakebin/restart-mode')
case "\$mode" in
  fail-all) exit 1 ;;
  backend-first) [[ "\$count" == 1 && "\$*" == *backend* ]] && exit 1 ;;
  frontend-first) [[ "\$count" == 2 && "\$*" == *frontend* ]] && exit 1 ;;
  bridge-first) [[ "\$count" == 3 && "\$*" == *bridge* ]] && exit 1 ;;
esac
exit 0
FAKE
  cat > "$fakebin/curl" <<FAKE
#!/usr/bin/env bash
printf 'curl %s\\n' "\$*" >> '$log_file'
count=\$((\$(cat '$fakebin/health-count') + 1))
printf '%s\\n' "\$count" > '$fakebin/health-count'
if [[ '$health_mode' == fail || '$health_mode' == fail-all ]]; then exit 22; fi
if [[ '$health_mode' == fail-first && "\$count" == 1 ]]; then exit 22; fi
exit 0
FAKE
  cat > "$fakebin/python3" <<FAKE
#!/usr/bin/env bash
printf 'python3 %s\\n' "\$*" >> '$log_file'
if [[ "\$*" == *' -m venv '* || "\$*" == *' venv '* ]]; then
  mkdir -p "\${@: -1}/bin"
  cat > "\${@: -1}/bin/python" <<'FAKEVENV'
#!/usr/bin/env bash
exit 0
FAKEVENV
  chmod +x "\${@: -1}/bin/python"
fi
exit 0
FAKE
  cat > "$fakebin/npm" <<FAKE
#!/usr/bin/env bash
printf 'npm %s\\n' "\$*" >> '$log_file'
exit 0
FAKE
  cat > "$fakebin/node" <<FAKE
#!/usr/bin/env bash
printf 'node %s\\n' "\$*" >> '$log_file'
exit 0
FAKE
  cat > "$fakebin/timeout" <<FAKE
#!/usr/bin/env bash
printf 'timeout %s\\n' "\$*" >> '$log_file'
shift
exec "\$@"
FAKE
  cat > "$fakebin/flock" <<FAKE
#!/usr/bin/env bash
printf 'flock %s\\n' "\$1" >> '$log_file'
shift
exec "\$@"
FAKE
  cat > "$fakebin/sudo" <<FAKE
#!/usr/bin/env bash
printf 'sudo %s\\n' "\$*" >> '$log_file'
[[ "\${1:-}" == -n ]] || exit 93
shift
exec "\$@"
FAKE
  cat > "$fakebin/sleep" <<FAKE
#!/usr/bin/env bash
printf 'sleep %s\\n' "\$*" >> '$log_file'
exit 0
FAKE
  chmod +x "$fakebin"/*
}

run_forced_command() {
  local prefix=$1 command=$2 stdin_file=${3:-/dev/null} extra_env=${4:-} allowed_env=${5:-dev}
  env -i \
    PREFIX="$prefix" \
    PATH="/usr/bin:/bin" \
    GPU_MONITOR_TEST_PATH="/usr/bin:/bin" \
    GPU_MONITOR_HEALTH_RETRIES=1 \
    GPU_MONITOR_HEALTH_SLEEP_SECONDS=1 \
    $extra_env \
    SSH_ORIGINAL_COMMAND="$command" \
    "$DEPLOY_COMMAND" --test-mode "$allowed_env" < "$stdin_file"
}

test_server_scripts_exist_before_security_tests() {
  [[ -x "$DEPLOY_COMMAND" ]] || fail "server forced-command wrapper is missing or not executable"
  [[ -x "$ACTIVATE_SCRIPT" ]] || fail "server activation script is missing or not executable"
  [[ -x "$HEALTH_SCRIPT" ]] || fail "server health-check script is missing or not executable"
  [[ -x "$INSTALLER_SCRIPT" ]] || fail "server installer script is missing or not executable"
  [[ -x "$RESTART_BROKER" ]] || fail "root-owned exact restart broker is missing or not executable"
  [[ -f "$DEV_SUDOERS" && -f "$LIVE_SUDOERS" ]] || fail "separate exact sudoers allowlists are missing"
  log "server scripts exist"
}

test_forced_command_rejects_open_grammar_and_env_crossing() {
  local tmp prefix sha digest artifact
  tmp=$(mktemp_dir gpu-release-forced-command)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"
  sha=0123456789abcdef0123456789abcdef01234567
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha"
  artifact="$tmp/artifact/gpu-monitor-$sha.tar.gz"
  digest=$(cat "$tmp/artifact/digest")

  for cmd in \
    "" \
    "status prod" \
    "status  dev" \
    $'status dev\nid' \
    "status dev now" \
    "upload dev ABC3456789abcdef0123456789abcdef01234567 $digest" \
    "upload dev $sha ${digest}00" \
    "activate dev $sha $digest; id" \
    "rollback ../live"; do
    if run_forced_command "$prefix" "$cmd" "$artifact" >/"$tmp/bad.out" 2>/"$tmp/bad.err"; then
      fail "unsafe forced command was accepted: $cmd"
    fi
  done

  if run_forced_command "$prefix" "status live" /dev/null "GPU_MONITOR_ALLOWED_ENV=live" dev >/"$tmp/cross.out" 2>/"$tmp/cross.err"; then
    fail "dev authorization boundary accepted live status"
  fi
  if env -i PREFIX="$prefix" PATH="/usr/bin:/bin" SSH_ORIGINAL_COMMAND="status live" "$DEPLOY_COMMAND" dev >/"$tmp/argv-cross.out" 2>/"$tmp/argv-cross.err"; then
    fail "dev forced-command argv boundary accepted live status"
  fi
  mkdir -p "$tmp/real-prefix"
  ln -s "$tmp/real-prefix" "$tmp/prefix-link"
  if run_forced_command "$tmp/prefix-link" "status dev" /dev/null "" dev >"$tmp/symlink-prefix.out" 2>"$tmp/symlink-prefix.err"; then
    fail "test-mode forced command accepted a symlink PREFIX"
  fi
  log "forced-command grammar and env authorization reject unsafe requests"
}

test_production_mode_scrubs_hostile_environment_before_dispatch() {
  local tmp fakebin marker hostile_prefix output_status
  tmp=$(mktemp_dir gpu-release-production-scrub)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  fakebin="$tmp/fakebin"
  marker="$tmp/hostile-command-ran"
  hostile_prefix="$tmp/hostile-prefix"
  mkdir -p "$fakebin"
  for command_name in python3 mkdir dirname; do
    printf '#!/usr/bin/env bash\ntouch %q\nexit 97\n' "$marker" > "$fakebin/$command_name"
    chmod +x "$fakebin/$command_name"
  done

  if env -i \
    PATH="$fakebin" \
    PREFIX="$hostile_prefix" \
    GPU_MONITOR_TEST_PATH="$fakebin" \
    GPU_MONITOR_MAX_UPLOAD_BYTES=1 \
    GPU_MONITOR_ALLOWED_ENV=live \
    GPU_MONITOR_INTERNAL_PYTHON="$fakebin/python3" \
    SSH_ORIGINAL_COMMAND="status dev" \
    "$DEPLOY_COMMAND" dev > "$tmp/status.out" 2> "$tmp/status.err"; then
    output_status=0
  else
    output_status=$?
  fi

  [[ "$output_status" -ne 0 ]] || fail "production forced command accepted a mismatched OS caller"
  grep -Fq 'gpu-deploy-dev' "$tmp/status.err" ||
    fail "production caller rejection did not name the required deploy identity"
  [[ ! -e "$marker" ]] || fail "hostile inherited PATH/internal Python altered production dispatch"
  [[ ! -e "$hostile_prefix" ]] || fail "hostile inherited PREFIX altered production root"
  log "production forced-command mode scrubs hostile overrides and binds the OS caller"
}

test_production_activator_rejects_mismatched_caller_before_root_mutation() {
  local tmp environment before after
  tmp=$(mktemp_dir gpu-release-production-caller)
  trap 'rm -rf "$tmp"' RETURN
  if [[ "$(id -un)" == gpu-deploy-dev ]]; then environment=live; else environment=dev; fi
  before=$(python3 - "/srv/gpu-monitor/$environment" "/var/lock/gpu-monitor/$environment" <<'PY'
import json, os, sys
def snapshot(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return [st.st_mode, st.st_uid, st.st_gid, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
print(json.dumps([snapshot(path) for path in sys.argv[1:]], separators=(",", ":")))
PY
)
  if env -i PATH=/usr/bin:/bin "$ACTIVATE_SCRIPT" status "$environment" >"$tmp/out" 2>"$tmp/err"; then
    fail "production activator accepted a mismatched effective username"
  fi
  grep -Fq "gpu-deploy-$environment" "$tmp/err" ||
    fail "production activator caller rejection did not name the required identity"
  after=$(python3 - "/srv/gpu-monitor/$environment" "/var/lock/gpu-monitor/$environment" <<'PY'
import json, os, sys
def snapshot(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    return [st.st_mode, st.st_uid, st.st_gid, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
print(json.dumps([snapshot(path) for path in sys.argv[1:]], separators=(",", ":")))
PY
)
  [[ "$before" == "$after" ]] || fail "caller rejection mutated production deployment roots"

  local prefix="$tmp/prefix"
  env -i PREFIX="$prefix" PATH=/usr/bin:/bin GPU_MONITOR_TEST_PATH=/usr/bin:/bin \
    "$ACTIVATE_SCRIPT" --test-mode "$environment" status >"$tmp/test-mode.out"
  grep -Fq "\"environment\":\"$environment\"" "$tmp/test-mode.out" ||
    fail "production caller binding leaked into isolated test mode"
  log "production activator rejects caller mismatch before root mutation while test mode stays isolated"
}

test_upload_is_bounded_digest_verified_and_cleans_failures() {
  local tmp prefix sha digest artifact bad_digest
  tmp=$(mktemp_dir gpu-release-upload)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"
  sha=1111111111111111111111111111111111111111
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha"
  artifact="$tmp/artifact/gpu-monitor-$sha.tar.gz"
  digest=$(cat "$tmp/artifact/digest")
  bad_digest=2222222222222222222222222222222222222222222222222222222222222222

  if run_forced_command "$prefix" "upload dev $sha $bad_digest" "$artifact" >/"$tmp/bad.out" 2>/"$tmp/bad.err"; then
    fail "upload accepted mismatched digest"
  fi
  [[ ! -e "$prefix/srv/gpu-monitor/dev/incoming/$sha/$bad_digest.tar.gz" ]] || fail "bad upload left incoming artifact"

  run_forced_command "$prefix" "upload dev $sha $digest" "$artifact" >/"$tmp/good.out" 2>/"$tmp/good.err"
  [[ -s "$prefix/srv/gpu-monitor/dev/incoming/$sha/$digest.tar.gz" ]] || fail "verified upload did not persist incoming artifact"

  sha=2222222222222222222222222222222222222222
  make_release_artifact "$tmp/artifact" "$sha" oversize
  artifact="$tmp/artifact/gpu-monitor-$sha.tar.gz"
  digest=$(cat "$tmp/artifact/digest")
  if run_forced_command "$prefix" "upload dev $sha $digest" "$artifact" "GPU_MONITOR_MAX_UPLOAD_BYTES=1" dev >/"$tmp/large.out" 2>/"$tmp/large.err"; then
    fail "upload accepted artifact over configured size bound"
  fi
  [[ ! -e "$prefix/srv/gpu-monitor/dev/incoming/$sha/$digest.tar.gz" ]] || fail "oversized upload left incoming artifact"

  for invalid_bound in 0 -1 536870913 not-a-number; do
    if run_forced_command "$prefix" "upload dev $sha $digest" "$artifact" "GPU_MONITOR_MAX_UPLOAD_BYTES=$invalid_bound" dev >/"$tmp/bound.out" 2>/"$tmp/bound.err"; then
      fail "test mode accepted invalid upload bound: $invalid_bound"
    fi
  done
  log "uploads are size-bounded, digest-verified, and cleaned on failure"
}

test_activation_dev_live_boundaries_pointers_units_and_rollback() {
  local tmp prefix fakebin log_file sha1 sha2 digest1 digest2 artifact
  tmp=$(mktemp_dir gpu-release-activate)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  sha1=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  sha2=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  mkdir -p "$tmp/a1" "$tmp/a2"
  make_release_artifact "$tmp/a1" "$sha1" one
  make_release_artifact "$tmp/a2" "$sha2" two
  digest1=$(cat "$tmp/a1/digest"); digest2=$(cat "$tmp/a2/digest")
  run_forced_command "$prefix" "upload dev $sha1 $digest1" "$tmp/a1/gpu-monitor-$sha1.tar.gz"
  run_forced_command "$prefix" "activate dev $sha1 $digest1" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/act1.out" 2>/"$tmp/act1.err"
  assert_symlink_target "$prefix/srv/gpu-monitor/dev/current" "releases/$sha1"
  [[ -e "$prefix/srv/gpu-monitor/dev/releases/$sha1/release-manifest.json" ]] || fail "server did not reconstruct manifest"
  grep -q 'gpu-monitor-backend@dev' "$log_file" || fail "dev backend unit was not restarted"
  grep -q 'gpu-monitor-frontend@dev' "$log_file" || fail "dev frontend unit was not restarted"
  ! grep -q 'gpu-monitor-bridge@dev\|gpu-monitor-.*@live' "$log_file" || fail "dev activation touched bridge or live units"
  grep -q "flock .*dev" "$log_file" || fail "dev env-specific flock was not used"
  : > "$log_file"
  run_forced_command "$prefix" "status dev" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/status-dev.out" 2>/"$tmp/status-dev.err"
  grep -q "flock .*dev" "$log_file" || fail "status did not use the dev env-specific flock"

  run_forced_command "$prefix" "upload live $sha2 $digest2" "$tmp/a2/gpu-monitor-$sha2.tar.gz" "" live
  run_forced_command "$prefix" "activate live $sha2 $digest2" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" live >/"$tmp/act-live.out" 2>/"$tmp/act-live.err"
  assert_symlink_target "$prefix/srv/gpu-monitor/live/current" "releases/$sha2"
  grep -q 'gpu-monitor-bridge@live' "$log_file" || fail "live activation did not check/restart bridge"
  grep -q "flock .*live" "$log_file" || fail "live env-specific flock was not used"
  [[ "$prefix/srv/gpu-monitor/dev" != "$prefix/srv/gpu-monitor/live" ]] || fail "dev/live roots overlap"

  # Existing immutable release must not be modified by a second activation of same SHA.
  local before after
  before=$(find "$prefix/srv/gpu-monitor/dev/releases/$sha1" -type f -print -exec shasum -a 256 {} \; | LC_ALL=C sort)
  run_forced_command "$prefix" "activate dev $sha1 $digest1" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/act-repeat.out" 2>/"$tmp/act-repeat.err"
  after=$(find "$prefix/srv/gpu-monitor/dev/releases/$sha1" -type f -print -exec shasum -a 256 {} \; | LC_ALL=C sort)
  [[ "$before" == "$after" ]] || fail "activation mutated existing immutable release"

  # A different artifact digest cannot be recorded against an already-published SHA.
  local conflicting_digest
  mkdir -p "$tmp/conflicting"
  make_release_artifact "$tmp/conflicting" "$sha1" conflicting
  conflicting_digest=$(cat "$tmp/conflicting/digest")
  if run_forced_command "$prefix" "upload dev $sha1 $conflicting_digest" "$tmp/conflicting/gpu-monitor-$sha1.tar.gz" >/"$tmp/conflicting.out" 2>/"$tmp/conflicting.err"; then
    fail "upload reused an existing SHA for a conflicting artifact digest"
  fi
  after=$(find "$prefix/srv/gpu-monitor/dev/releases/$sha1" -type f -print -exec shasum -a 256 {} \; | LC_ALL=C sort)
  [[ "$before" == "$after" ]] || fail "conflicting activation mutated existing immutable release"

  # Failed health must restore previous pointer.
  local sha3 digest3
  sha3=cccccccccccccccccccccccccccccccccccccccc
  mkdir -p "$tmp/a3"
  make_release_artifact "$tmp/a3" "$sha3" three
  digest3=$(cat "$tmp/a3/digest")
  run_forced_command "$prefix" "upload dev $sha3 $digest3" "$tmp/a3/gpu-monitor-$sha3.tar.gz"
  rm -rf "$fakebin"; : > "$log_file"; install_fake_server_commands "$fakebin" "$log_file" fail-first
  if run_forced_command "$prefix" "activate dev $sha3 $digest3" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/act-fail.out" 2>/"$tmp/act-fail.err"; then
    fail "activation succeeded despite failing health"
  fi
  assert_symlink_target "$prefix/srv/gpu-monitor/dev/current" "releases/$sha1"
  grep -q '"status":"rollback_succeeded"' "$prefix/srv/gpu-monitor/dev/deployments.jsonl" || fail "rollback state was not recorded"
  log "activation isolates envs, uses atomic pointers/units/flocks, and rolls back on failed health"
}

test_first_activation_failure_restores_absent_pointer_state() {
  local tmp prefix fakebin log_file sha digest
  tmp=$(mktemp_dir gpu-release-first-rollback)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"
  fakebin="$tmp/fakebin"
  log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" fail-first
  sha=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha" first
  digest=$(cat "$tmp/artifact/digest")

  run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/artifact/gpu-monitor-$sha.tar.gz"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev > "$tmp/activate.out" 2> "$tmp/activate.err"; then
    fail "first activation succeeded despite failing health"
  fi
  [[ ! -e "$prefix/srv/gpu-monitor/dev/current" ]] || fail "failed first activation left a current pointer"
  [[ ! -e "$prefix/srv/gpu-monitor/dev/previous" ]] || fail "failed first activation changed absent previous pointer state"
  log "failed first activation restores exactly absent current and previous pointers"
}

test_health_test_overrides_are_positive_and_bounded() {
  local tmp fakebin log_file name value
  tmp=$(mktemp_dir gpu-release-health-bounds)
  trap 'rm -rf "$tmp"' RETURN
  fakebin="$tmp/fakebin"
  log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass

  for name in GPU_MONITOR_HEALTH_RETRIES GPU_MONITOR_HEALTH_SLEEP_SECONDS; do
    for value in 0 -1 not-a-number 999999; do
      if env -i \
        PATH="/usr/bin:/bin" \
        GPU_MONITOR_TEST_PATH="$fakebin:/usr/bin:/bin" \
        "$name=$value" \
        "$HEALTH_SCRIPT" --test-mode dev > "$tmp/health.out" 2> "$tmp/health.err"; then
        fail "health test mode accepted invalid $name=$value"
      fi
    done
  done
  log "health retry overrides are validated as bounded positive integers"
}

test_systemd_units_match_real_runtime_entrypoints() {
  local backend_unit bridge_unit frontend_unit
  backend_unit="$SERVER_DIR/systemd/gpu-monitor-backend@.service"
  bridge_unit="$SERVER_DIR/systemd/gpu-monitor-bridge@.service"
  frontend_unit="$SERVER_DIR/systemd/gpu-monitor-frontend@.service"

  grep -Fq 'uvicorn[standard]==' "$SOURCE_ROOT/apps/gpu-monitor/backend/requirements.txt" ||
    fail "real backend requirements do not provide uvicorn"
  grep -Fq 'app = FastAPI' "$SOURCE_ROOT/apps/gpu-monitor/backend/main.py" ||
    fail "real backend module does not expose backend.main:app"
  grep -Fq 'app = FastAPI' "$SOURCE_ROOT/apps/gpu-monitor/backend/slack_bridge.py" ||
    fail "real bridge module does not expose backend.slack_bridge:app"
  python3 - "$SOURCE_ROOT/apps/gpu-monitor/frontend/package.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["scripts"]["build"] == "vite build"
assert "start" not in data["scripts"]
PY
  grep -Fq -- '-m uvicorn backend.main:app --host 127.0.0.1 --port ${GPU_MONITOR_BACKEND_PORT}' "$backend_unit" ||
    fail "backend unit does not run the real FastAPI module through uvicorn with configured port"
  grep -Fq -- '-m uvicorn backend.slack_bridge:app --host 127.0.0.1 --port ${GPU_MONITOR_BRIDGE_PORT}' "$bridge_unit" ||
    fail "bridge unit does not run the real FastAPI bridge module through uvicorn with configured port"
  grep -Fq 'ExecStart=/opt/gpu-monitor/node/bin/node /srv/gpu-monitor/%i/current/frontend/build/index.js' "$frontend_unit" ||
    fail "frontend unit does not run the built adapter-node entrypoint with the managed Node runtime"
  ! grep -Fq '/usr/bin/node' "$frontend_unit" ||
    fail "frontend unit still depends on the host distribution Node runtime"
  grep -Fq 'Environment=HOST=127.0.0.1' "$frontend_unit" ||
    fail "frontend unit does not bind adapter-node to loopback"
  log "systemd units match the real backend modules and built Svelte adapter-node runtime"
}

test_state_appends_use_crash_durable_writer() {
  grep -Fq 'os.fsync' "$ACTIVATE_SCRIPT" || fail "deployment state writer does not fsync JSONL appends"
  grep -Fq 'os.O_DIRECTORY' "$ACTIVATE_SCRIPT" || fail "first deployment state creation does not fsync its parent directory"
  if grep -Eq 'json_line .*>>.*state' "$ACTIVATE_SCRIPT"; then
    fail "deployment state still uses non-durable shell redirection"
  fi
  log "deployment JSONL state appends use the crash-durable writer"
}


test_directory_mutations_are_fsynced() {
  grep -Fq 'fsync_directory()' "$ACTIVATE_SCRIPT" || fail "activation script has no reusable directory fsync primitive"
  for needle in \
    'fsync_directory "$base"' \
    'fsync_directory "$releases"' \
    'fsync_directory "$incoming"' \
    'fsync_directory "$tmp_root"' \
    'fsync_tree_bottom_up "$release"' \
    'fsync_regular_files "$release"' \
    'fsync_directory "$generations"'; do
    grep -Fq "$needle" "$ACTIVATE_SCRIPT" || fail "directory mutation is not durably fsynced: $needle"
  done
  log "release contents and directory mutations are fsynced before atomic publication and pointer changes"
}

test_isolated_identities_broker_and_descriptor_extraction_contract() {
  for unit in "$SERVER_DIR"/systemd/*.service; do
    grep -Fq 'User=gpu-monitor-%i' "$unit" || fail "$unit does not use environment-specific runtime user"
    grep -Fq 'Group=gpu-monitor-%i' "$unit" || fail "$unit does not use environment-specific runtime group"
    grep -Fq 'ProtectProc=invisible' "$unit" || fail "$unit does not hide unrelated processes where systemd supports it"
    ! grep -Fq 'gpu-deploy-%i' "$unit" || fail "$unit still runs services as deploy identity"
  done
  grep -Fxq 'gpu-deploy-dev ALL=(root) NOPASSWD: /usr/local/libexec/gpu-monitor-restart-broker dev' "$DEV_SUDOERS" ||
    fail "dev sudoers is not the exact dev-only broker allowlist"
  grep -Fxq 'gpu-deploy-live ALL=(root) NOPASSWD: /usr/local/libexec/gpu-monitor-restart-broker live' "$LIVE_SUDOERS" ||
    fail "live sudoers is not the exact live-only broker allowlist"
  ! grep -Fq ' live' "$DEV_SUDOERS" || fail "dev sudoers grants a live restart capability"
  ! grep -Fq ' dev' "$LIVE_SUDOERS" || fail "live sudoers grants a dev restart capability"
  grep -Fq '/usr/bin/sudo -n "$restart_broker" "$env"' "$ACTIVATE_SCRIPT" ||
    fail "production activation does not call only sudo -n and the exact broker"
  ! grep -Eq 'getmembers|extractall|tarfile\.open\([^f]' "$ACTIVATE_SCRIPT" ||
    fail "archive implementation uses bulk enumeration/extraction or pathname reopen"
  grep -Fq 'dir_fd=object_dir_fd' "$ACTIVATE_SCRIPT" || fail "incoming artifact is not opened relative to incoming dirfd"
  grep -Fq 'O_NOFOLLOW' "$ACTIVATE_SCRIPT" || fail "incoming artifact open is not no-follow"
  grep -Fq 'stat.S_ISREG' "$ACTIVATE_SCRIPT" || fail "incoming artifact fd is not regular-file checked"
  grep -Fq 'tarfile.open(fileobj=artifact_file, mode="r|gz")' "$ACTIVATE_SCRIPT" ||
    fail "archive extraction is not streamed from the already-hashed descriptor"
  log "isolated runtime identities, exact broker allowlists, and dirfd descriptor-bound extraction are contractual"
}

test_restart_broker_exact_unit_allowlist() {
  local tmp fakebin log_file
  tmp=$(mktemp_dir gpu-release-broker)
  trap 'rm -rf "$tmp"' RETURN
  fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass

  env -i PATH=/usr/bin:/bin GPU_MONITOR_TEST_PATH="$fakebin:/usr/bin:/bin" \
    "$RESTART_BROKER" --test-mode dev
  grep -Fq 'gpu-monitor-backend@dev.service' "$log_file" || fail "dev broker omitted backend"
  grep -Fq 'gpu-monitor-frontend@dev.service' "$log_file" || fail "dev broker omitted frontend"
  ! grep -Eq 'bridge|@live' "$log_file" || fail "dev broker widened into bridge/live"

  : > "$log_file"; printf '0\n' > "$fakebin/restart-count"
  env -i PATH=/usr/bin:/bin GPU_MONITOR_TEST_PATH="$fakebin:/usr/bin:/bin" \
    "$RESTART_BROKER" --test-mode live
  grep -Fq 'gpu-monitor-bridge@live.service' "$log_file" || fail "live broker omitted bridge"
  if "$RESTART_BROKER" dev live > "$tmp/bad.out" 2> "$tmp/bad.err"; then
    fail "broker accepted a widened command shape"
  fi
  log "restart broker maps exact environments to exact selected units"
}

test_transaction_restores_both_pointers_for_restart_health_and_manual_failures() {
  local case_name env_name restart_mode health_mode expected_status tmp prefix fakebin log_file
  local sha1 sha2 sha3 digest1 digest2 digest3
  for case_name in backend frontend bridge health recovery manual; do
    tmp=$(mktemp_dir "gpu-release-transaction-$case_name")
    prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
    env_name=dev; restart_mode=pass; health_mode=pass; expected_status=rollback_succeeded
    [[ "$case_name" == backend ]] && restart_mode=backend-first
    [[ "$case_name" == frontend ]] && restart_mode=frontend-first
    [[ "$case_name" == bridge ]] && { env_name=live; restart_mode=bridge-first; }
    [[ "$case_name" == health ]] && health_mode=fail-first
    [[ "$case_name" == recovery ]] && { restart_mode=fail-all; expected_status=rollback_failed; }
    [[ "$case_name" == manual ]] && restart_mode=frontend-first
    install_fake_server_commands "$fakebin" "$log_file" pass
    sha1=1111111111111111111111111111111111111111
    sha2=2222222222222222222222222222222222222222
    sha3=3333333333333333333333333333333333333333
    mkdir -p "$tmp/a1" "$tmp/a2" "$tmp/a3"
    make_release_artifact "$tmp/a1" "$sha1" one
    make_release_artifact "$tmp/a2" "$sha2" two
    make_release_artifact "$tmp/a3" "$sha3" three
    digest1=$(cat "$tmp/a1/digest"); digest2=$(cat "$tmp/a2/digest"); digest3=$(cat "$tmp/a3/digest")
    run_forced_command "$prefix" "upload $env_name $sha1 $digest1" "$tmp/a1/gpu-monitor-$sha1.tar.gz" "" "$env_name"
    run_forced_command "$prefix" "activate $env_name $sha1 $digest1" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" "$env_name"
    run_forced_command "$prefix" "upload $env_name $sha2 $digest2" "$tmp/a2/gpu-monitor-$sha2.tar.gz" "" "$env_name"
    run_forced_command "$prefix" "activate $env_name $sha2 $digest2" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" "$env_name"
    assert_symlink_target "$prefix/srv/gpu-monitor/$env_name/current" "releases/$sha2"
    assert_symlink_target "$prefix/srv/gpu-monitor/$env_name/previous" "releases/$sha1"

    rm -rf "$fakebin"; : > "$log_file"
    install_fake_server_commands "$fakebin" "$log_file" "$health_mode" "$restart_mode"
    if [[ "$case_name" == manual ]]; then
      if run_forced_command "$prefix" "rollback $env_name" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" "$env_name"; then
        fail "manual rollback succeeded despite injected restart failure"
      fi
    else
      run_forced_command "$prefix" "upload $env_name $sha3 $digest3" "$tmp/a3/gpu-monitor-$sha3.tar.gz" "" "$env_name"
      if run_forced_command "$prefix" "activate $env_name $sha3 $digest3" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" "$env_name"; then
        fail "$case_name activation succeeded despite injected failure"
      fi
    fi
    assert_symlink_target "$prefix/srv/gpu-monitor/$env_name/current" "releases/$sha2"
    assert_symlink_target "$prefix/srv/gpu-monitor/$env_name/previous" "releases/$sha1"
    grep -Fq "\"status\":\"$expected_status\"" "$prefix/srv/gpu-monitor/$env_name/deployments.jsonl" ||
      fail "$case_name did not durably record $expected_status"
    [[ "$(grep -c '^sudo -n ' "$log_file")" -ge 2 ]] || fail "$case_name did not attempt guarded candidate and recovery restarts"
    chmod -R u+w "$tmp" 2>/dev/null || true
    rm -rf "$tmp"
    trap - RETURN
  done
  log "activation and manual rollback restore exact pointer snapshots across unit/health/recovery failures"
}

test_recovery_pointer_and_fsync_failures_record_rollback_failed() {
  local failure tmp prefix fakebin log_file marker python_wrapper sha1 sha2 sha3 digest1 digest2 digest3 state
  for failure in generation-swap generation-fsync; do
    tmp=$(mktemp_dir "gpu-release-recovery-$failure")
    prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
    marker="$tmp/recovery-started"; python_wrapper="$tmp/injecting-python"
    install_fake_server_commands "$fakebin" "$log_file" pass
    printf '%s\n' "$failure" > "$python_wrapper.failure"
    printf '%s\n' "$marker" > "$python_wrapper.marker"
    printf '%s\n' "$prefix/srv/gpu-monitor/dev/generations" > "$python_wrapper.generations"
    cat > "$python_wrapper" <<'PYWRAPPER'
#!/usr/bin/env bash
set -euo pipefail
real_python=/usr/bin/python3
failure=$(cat "$0.failure")
marker=$(cat "$0.marker")
generations=$(cat "$0.generations")
if [[ "${1:-}" != - ]]; then exec "$real_python" "$@"; fi
script=$(mktemp "${TMPDIR:-/tmp}/gpu-monitor-python.XXXXXX")
trap 'rm -f "$script"' EXIT
/bin/cat > "$script"
if [[ -e "$marker" ]]; then
  if [[ "$failure" == generation-swap && "${3:-}" == "$generations/active" ]] &&
      /usr/bin/grep -Fq 'os.replace(sys.argv[1], sys.argv[2])' "$script"; then
    exit 91
  fi
  if [[ "$failure" == generation-fsync && "${2:-}" == "$generations" ]] &&
      /usr/bin/grep -Fq 'os.fsync(fd)' "$script"; then
    exit 92
  fi
fi
exec "$real_python" "$script" "${@:2}"
PYWRAPPER
    chmod +x "$python_wrapper"
    sha1=1212121212121212121212121212121212121212
    sha2=2323232323232323232323232323232323232323
    sha3=3434343434343434343434343434343434343434
    mkdir -p "$tmp/a1" "$tmp/a2" "$tmp/a3"
    make_release_artifact "$tmp/a1" "$sha1" one
    make_release_artifact "$tmp/a2" "$sha2" two
    make_release_artifact "$tmp/a3" "$sha3" three
    digest1=$(cat "$tmp/a1/digest"); digest2=$(cat "$tmp/a2/digest"); digest3=$(cat "$tmp/a3/digest")
    run_forced_command "$prefix" "upload dev $sha1 $digest1" "$tmp/a1/gpu-monitor-$sha1.tar.gz"
    run_forced_command "$prefix" "activate dev $sha1 $digest1" /dev/null \
      "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_INTERNAL_PYTHON=$python_wrapper"
    run_forced_command "$prefix" "upload dev $sha2 $digest2" "$tmp/a2/gpu-monitor-$sha2.tar.gz"
    run_forced_command "$prefix" "activate dev $sha2 $digest2" /dev/null \
      "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_INTERNAL_PYTHON=$python_wrapper"
    run_forced_command "$prefix" "upload dev $sha3 $digest3" "$tmp/a3/gpu-monitor-$sha3.tar.gz"
    cat > "$fakebin/curl" <<FAKECURL
#!/usr/bin/env bash
touch '$marker'
exit 22
FAKECURL
    chmod +x "$fakebin/curl"
    if run_forced_command "$prefix" "activate dev $sha3 $digest3" /dev/null \
      "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_INTERNAL_PYTHON=$python_wrapper" \
      >"$tmp/activate.out" 2>"$tmp/activate.err"; then
      fail "$failure recovery injection unexpectedly succeeded"
    fi
    state="$prefix/srv/gpu-monitor/dev/deployments.jsonl"
    grep -Fq '"status":"rollback_failed"' "$state" ||
      fail "$failure restoration error was not durably recorded as rollback_failed"
    ! tail -1 "$state" | grep -Fq '"status":"rollback_succeeded"' ||
      fail "$failure restoration error was falsely recorded as rollback_succeeded"
    [[ -d "$prefix/srv/gpu-monitor/dev/releases/$sha3" ]] ||
      fail "$failure recovery deleted a candidate before recovery was proven successful"
    if [[ "$failure" == generation-swap ]]; then
      assert_symlink_target "$prefix/srv/gpu-monitor/dev/current" "releases/$sha3"
    fi
    chmod -R u+w "$tmp" 2>/dev/null || true
    rm -rf "$tmp"
    trap - RETURN
  done
  log "generation swap and fsync restoration failures durably record rollback_failed"
}

run_dependency_failure_case() {
  local failure=$1 sha=$2 tmp prefix fakebin log_file digest release
  tmp=$(mktemp_dir "gpu-release-dependency-$failure")
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha" "$failure"
  digest=$(cat "$tmp/artifact/digest")
  run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/artifact/gpu-monitor-$sha.tar.gz"
  case "$failure" in
    venv-timeout|pip-timeout|npm-timeout)
      printf '%s\n' "$failure" > "$fakebin/timeout-failure"
      cat > "$fakebin/timeout" <<FAKETIMEOUT
#!/usr/bin/env bash
printf 'timeout %s\\n' "\$*" >> '$log_file'
failure=\$(cat '$fakebin/timeout-failure')
shift
case "\$failure:\$*" in
  venv-timeout:*' -m venv '*) exit 91 ;;
  pip-timeout:*' -m pip '*) exit 92 ;;
  npm-timeout:*'/npm ci '*) exit 93 ;;
esac
exec "\$@"
FAKETIMEOUT
      chmod +x "$fakebin/timeout"
      ;;
    missing-venv)
      cat > "$fakebin/python3" <<FAKEPYTHON
#!/usr/bin/env bash
printf 'python3 %s\\n' "\$*" >> '$log_file'
exit 0
FAKEPYTHON
      chmod +x "$fakebin/python3"
      ;;
    missing-node)
      rm -f "$fakebin/node"
      ;;
  esac
  : > "$log_file"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" >"$tmp/activate.out" 2>"$tmp/activate.err"; then
    fail "$failure dependency failure was masked"
  fi
  release="$prefix/srv/gpu-monitor/dev/releases/$sha"
  [[ ! -e "$release" ]] || fail "$failure dependency failure published a release"
  [[ ! -e "$prefix/srv/gpu-monitor/dev/current" ]] ||
    fail "$failure dependency failure changed current"
  ! find "$prefix/srv/gpu-monitor/dev/tmp" -mindepth 1 -maxdepth 1 -name 'release-*' | grep -q . ||
    fail "$failure dependency failure left a staging candidate"
  trap - RETURN
  chmod -R u+w "$tmp" 2>/dev/null || true
  rm -rf "$tmp"
}

test_dependency_venv_timeout_failure_is_not_published() {
  run_dependency_failure_case venv-timeout 5656565656565656565656565656565656565656
  log "venv timeout failure is propagated and not published"
}

test_dependency_pip_timeout_failure_is_not_published() {
  run_dependency_failure_case pip-timeout 6767676767676767676767676767676767676767
  log "pip timeout failure is propagated and not published"
}

test_dependency_npm_timeout_failure_is_not_published() {
  run_dependency_failure_case npm-timeout 7878787878787878787878787878787878787878
  log "npm timeout failure is propagated and not published"
}

test_dependency_missing_venv_interpreter_is_not_published() {
  run_dependency_failure_case missing-venv 8989898989898989898989898989898989898989
  log "missing venv interpreter is rejected and not published"
}

test_dependency_missing_frontend_runtime_is_not_published() {
  run_dependency_failure_case missing-node 9090909090909090909090909090909090909090
  log "missing frontend runtime prerequisite is rejected and not published"
}

test_dependency_install_requires_explicit_trusted_timeout() {
  local tmp prefix fakebin log_file sha digest
  tmp=$(mktemp_dir gpu-release-timeout-required)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  sha=4545454545454545454545454545454545454545
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha" timeout-required
  digest=$(cat "$tmp/artifact/digest")
  run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/artifact/gpu-monitor-$sha.tar.gz"
  rm -f "$fakebin/timeout"
  : > "$log_file"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" >"$tmp/activate.out" 2>"$tmp/activate.err"; then
    fail "activation ran dependency installation without an explicit trusted timeout"
  fi
  ! grep -Eq '^(python3 .*venv|npm )' "$log_file" ||
    fail "dependency command ran before missing timeout was rejected"
  grep -Eiq 'timeout.*(required|unavailable|invalid)' "$tmp/activate.err" ||
    fail "missing timeout rejection was not explicit"
  grep -Fq 'timeout_command=/usr/bin/timeout' "$ACTIVATE_SCRIPT" ||
    fail "production dependency installation does not use the fixed trusted timeout path"
  ! grep -Eq 'command -v (g?timeout)|elif .*gtimeout|else[[:space:]]+python3 -m venv' "$ACTIVATE_SCRIPT" ||
    fail "dependency installation still has an untrusted or unbounded timeout fallback"
  log "dependency installation fails closed without an explicit trusted timeout"
}

test_incoming_content_addressing_quotas_and_success_cleanup() {
  local tmp prefix fakebin log_file sha1 sha2 sha3 digest1 digest2 digest3
  tmp=$(mktemp_dir gpu-release-incoming-control)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  sha1=4444444444444444444444444444444444444444
  sha2=5555555555555555555555555555555555555555
  sha3=6666666666666666666666666666666666666666
  mkdir -p "$tmp/a1" "$tmp/a2" "$tmp/a3"
  make_release_artifact "$tmp/a1" "$sha1" one
  make_release_artifact "$tmp/a2" "$sha2" two
  make_release_artifact "$tmp/a3" "$sha3" three
  digest1=$(cat "$tmp/a1/digest"); digest2=$(cat "$tmp/a2/digest"); digest3=$(cat "$tmp/a3/digest")
  local quota_env='GPU_MONITOR_MAX_INCOMING_COUNT=2 GPU_MONITOR_MAX_INCOMING_BYTES=1073741824'
  run_forced_command "$prefix" "upload dev $sha1 $digest1" "$tmp/a1/gpu-monitor-$sha1.tar.gz" "$quota_env"
  run_forced_command "$prefix" "upload dev $sha1 $digest1" "$tmp/a1/gpu-monitor-$sha1.tar.gz" "$quota_env"
  [[ "$(find "$prefix/srv/gpu-monitor/dev/incoming" -name '*.tar.gz' | wc -l | tr -d ' ')" == 1 ]] ||
    fail "repeated identical upload consumed additional quota"
  run_forced_command "$prefix" "upload dev $sha2 $digest2" "$tmp/a2/gpu-monitor-$sha2.tar.gz" "$quota_env"
  if run_forced_command "$prefix" "upload dev $sha3 $digest3" "$tmp/a3/gpu-monitor-$sha3.tar.gz" "$quota_env"; then
    fail "dev incoming count quota accepted count limit + 1"
  fi
  run_forced_command "$prefix" "upload live $sha3 $digest3" "$tmp/a3/gpu-monitor-$sha3.tar.gz" "$quota_env" live ||
    fail "dev quota incorrectly consumed live quota"
  mkdir -p "$prefix/srv/gpu-monitor/dev/tmp"
  touch -t 200001010000 "$prefix/srv/gpu-monitor/dev/tmp/upload-stale"
  run_forced_command "$prefix" "activate dev $sha1 $digest1" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_UPLOAD_TEMP_MAX_AGE=1" dev
  [[ ! -e "$prefix/srv/gpu-monitor/dev/incoming/$sha1/$digest1.tar.gz" ]] ||
    fail "successful activation did not consume its incoming object"
  [[ ! -e "$prefix/srv/gpu-monitor/dev/tmp/upload-stale" ]] || fail "stale upload temp was not pruned under lock"
  log "incoming artifacts are content-addressed, quota-isolated, idempotent, pruned, and consumed"
}


test_incoming_artifact_open_rejects_symlink_and_fifo_without_hanging() {
  local tmp prefix fakebin log_file sha digest artifact incoming_object
  tmp=$(mktemp_dir gpu-release-incoming-open)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  sha=abababababababababababababababababababab
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha" incoming-open
  artifact="$tmp/artifact/gpu-monitor-$sha.tar.gz"
  digest=$(cat "$tmp/artifact/digest")

  run_forced_command "$prefix" "upload dev $sha $digest" "$artifact"
  incoming_object="$prefix/srv/gpu-monitor/dev/incoming/$sha/$digest.tar.gz"
  rm -f "$incoming_object"
  ln -s /etc/passwd "$incoming_object"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev > "$tmp/symlink.out" 2> "$tmp/symlink.err"; then
    fail "activation followed an incoming symlink artifact"
  fi

  rm -f "$incoming_object"
  if command -v mkfifo >/dev/null 2>&1; then
    mkfifo "$incoming_object"
    if TEST_TIMEOUT_SECONDS=5 run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
      "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev > "$tmp/fifo.out" 2> "$tmp/fifo.err"; then
      fail "activation accepted an incoming FIFO artifact"
    fi
    grep -Eiq 'regular|fifo|artifact|failed|ERROR' "$tmp/fifo.err" || fail "FIFO rejection did not explain nonregular artifact"
  fi
  log "incoming artifact open rejects symlink and FIFO objects without blocking"
}

test_archive_rejects_all_nonregular_types_conflicts_and_limit_plus_one() {
  local tmp prefix fakebin log_file kind index sha artifact digest bound
  tmp=$(mktemp_dir gpu-release-archive-types)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  index=10
  for kind in symlink hardlink fifo char block duplicate parent-file child-file; do
    sha=$(printf '%040d' "$index"); index=$((index + 1))
    artifact="$tmp/$sha.tar.gz"
    python3 - "$artifact" "$kind" <<'PY'
import io, tarfile, sys
path, kind = sys.argv[1:]
with tarfile.open(path, "w:gz") as tar:
    for name in ("gpu-monitor", "gpu-monitor/backend"):
        item = tarfile.TarInfo(name); item.type = tarfile.DIRTYPE; tar.addfile(item)
    if kind == "duplicate":
        for data in (b"a", b"b"):
            item = tarfile.TarInfo("gpu-monitor/backend/main.py"); item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    elif kind == "parent-file":
        data = b"x"; item = tarfile.TarInfo("gpu-monitor/frontend"); item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
        child = tarfile.TarInfo("gpu-monitor/frontend/build"); child.type = tarfile.DIRTYPE; tar.addfile(child)
    elif kind == "child-file":
        data = b"x"; item = tarfile.TarInfo("gpu-monitor/frontend/build/index.js"); item.size = len(data)
        tar.addfile(item, io.BytesIO(data))
        parent = tarfile.TarInfo("gpu-monitor/frontend"); parent.size = len(data)
        tar.addfile(parent, io.BytesIO(data))
    else:
        item = tarfile.TarInfo("gpu-monitor/backend/unsafe")
        item.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                     "fifo": tarfile.FIFOTYPE, "char": tarfile.CHRTYPE,
                     "block": tarfile.BLKTYPE}[kind]
        item.linkname = "gpu-monitor/backend/main.py"
        tar.addfile(item)
PY
    digest=$(sha256_file "$artifact" | awk '{print $1}')
    run_forced_command "$prefix" "upload dev $sha $digest" "$artifact"
    if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
      "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev > "$tmp/$kind.out" 2> "$tmp/$kind.err"; then
      fail "archive accepted forbidden $kind entry/conflict"
    fi
  done

  sha=9999999999999999999999999999999999999999
  mkdir -p "$tmp/valid"
  make_release_artifact "$tmp/valid" "$sha" limits
  artifact="$tmp/valid/gpu-monitor-$sha.tar.gz"; digest=$(cat "$tmp/valid/digest")
  bound=$(tar -tzf "$artifact" | wc -l | tr -d ' ')
  run_forced_command "$prefix" "upload dev $sha $digest" "$artifact"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_MAX_ARCHIVE_FILES=$((bound - 1))" dev; then
    fail "archive accepted entry count limit + 1"
  fi
  chmod -R u+w "$prefix/srv/gpu-monitor/dev" 2>/dev/null || true
  rm -rf "$prefix/srv/gpu-monitor/dev/releases/$sha"
  bound=$(python3 - "$artifact" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    print(sum(item.size for item in archive if item.isfile()))
PY
)
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_MAX_EXPANDED_BYTES=$((bound - 1))" dev; then
    fail "archive accepted expanded-byte limit + 1"
  fi
  log "archive rejects every non-file/directory type, path conflicts, duplicates, and both limit+1 cases"
}


test_generation_pointer_model_and_failed_candidate_cleanup() {
  local tmp prefix fakebin log_file sha1 sha2 sha3 digest1 digest2 digest3
  tmp=$(mktemp_dir gpu-release-generation-model)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  sha1=7777777777777777777777777777777777777777
  sha2=8888888888888888888888888888888888888888
  sha3=9999999999999999999999999999999999999998
  mkdir -p "$tmp/a1" "$tmp/a2" "$tmp/a3"
  make_release_artifact "$tmp/a1" "$sha1" one; digest1=$(cat "$tmp/a1/digest")
  make_release_artifact "$tmp/a2" "$sha2" two; digest2=$(cat "$tmp/a2/digest")
  make_release_artifact "$tmp/a3" "$sha3" three; digest3=$(cat "$tmp/a3/digest")
  run_forced_command "$prefix" "upload dev $sha1 $digest1" "$tmp/a1/gpu-monitor-$sha1.tar.gz"
  run_forced_command "$prefix" "activate dev $sha1 $digest1" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin"
  first_active=$(readlink "$prefix/srv/gpu-monitor/dev/generations/active")
  [[ -L "$prefix/srv/gpu-monitor/dev/current" && "$(readlink "$prefix/srv/gpu-monitor/dev/current")" == generations/active/current ]] ||
    fail "root current is not resolved through the active generation"
  assert_mode "$prefix/srv/gpu-monitor/dev/releases/$sha1" 0550
  assert_mode "$prefix/srv/gpu-monitor/dev/generations/$first_active" 0550
  canon_prefix=$(cd "$prefix" && pwd -P)
  [[ "$(cd "$prefix/srv/gpu-monitor/dev/current" && pwd -P)" == "$canon_prefix/srv/gpu-monitor/dev/releases/$sha1" ]] ||
    fail "root current does not resolve to the active release"
  run_forced_command "$prefix" "upload dev $sha2 $digest2" "$tmp/a2/gpu-monitor-$sha2.tar.gz"
  run_forced_command "$prefix" "activate dev $sha2 $digest2" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin"
  [[ "$(readlink "$prefix/srv/gpu-monitor/dev/generations/active")" != "$first_active" ]] || fail "activation did not atomically swap a new generation"
  [[ "$(cd "$prefix/srv/gpu-monitor/dev/current" && pwd -P)" == "$canon_prefix/srv/gpu-monitor/dev/releases/$sha2" ]] || fail "current generation resolution is wrong"
  [[ "$(cd "$prefix/srv/gpu-monitor/dev/previous" && pwd -P)" == "$canon_prefix/srv/gpu-monitor/dev/releases/$sha1" ]] || fail "previous generation resolution is wrong"
  rm -rf "$fakebin"; : > "$log_file"; install_fake_server_commands "$fakebin" "$log_file" fail-first pass
  run_forced_command "$prefix" "upload dev $sha3 $digest3" "$tmp/a3/gpu-monitor-$sha3.tar.gz"
  if run_forced_command "$prefix" "activate dev $sha3 $digest3" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin"; then
    fail "failed health activation unexpectedly succeeded"
  fi
  [[ ! -d "$prefix/srv/gpu-monitor/dev/releases/$sha3" ]] || fail "failed inactive candidate release was not removed"
  [[ "$(cd "$prefix/srv/gpu-monitor/dev/current" && pwd -P)" == "$canon_prefix/srv/gpu-monitor/dev/releases/$sha2" ]] || fail "failed activation changed current resolution"
  [[ "$(cd "$prefix/srv/gpu-monitor/dev/previous" && pwd -P)" == "$canon_prefix/srv/gpu-monitor/dev/releases/$sha1" ]] || fail "failed activation changed previous resolution"
  log "generation pointer swaps are atomic and failed inactive candidates are cleaned"
}

test_candidate_stages_privately_in_tmp_and_publishes_verified_runtime_gid() {
  local tmp prefix fakebin log_file marker continue sha digest activation_pid candidate
  tmp=$(mktemp_dir gpu-release-private-staging)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/install"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  marker="$tmp/dependency-started"; continue="$tmp/continue"
  sha=9191919191919191919191919191919191919191
  install_fake_server_commands "$fakebin" "$log_file" pass
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" \
    --dev-public-key 'ssh-ed25519 AAAAPRIVATESTAGINGKEY000000000000000000000000000000000000 private' \
    >"$tmp/install.out"
  assert_mode "$prefix/srv/gpu-monitor/dev/tmp" 2700
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha" private-staging
  digest=$(cat "$tmp/artifact/digest")
  run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/artifact/gpu-monitor-$sha.tar.gz"
  cat > "$fakebin/timeout" <<FAKETIMEOUT
#!/usr/bin/env bash
if [[ "\$*" == *' -m venv '* ]]; then
  touch '$marker'
  while [[ ! -e '$continue' ]]; do /bin/sleep 0.05; done
fi
shift
exec "\$@"
FAKETIMEOUT
  chmod +x "$fakebin/timeout"
  run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" >"$tmp/activate.out" 2>"$tmp/activate.err" &
  activation_pid=$!
  for _ in $(seq 1 100); do
    [[ -e "$marker" ]] && break
    /bin/sleep 0.05
  done
  [[ -e "$marker" ]] || fail "activation did not reach dependency staging"
  candidate=$(find "$prefix/srv/gpu-monitor/dev/tmp" -mindepth 1 -maxdepth 1 -type d -name 'release-*' | head -1)
  [[ -n "$candidate" ]] || fail "release candidate was not constructed under private tmp staging"
  ! find "$prefix/srv/gpu-monitor/dev/releases" -mindepth 1 -maxdepth 1 -name '.release-*' | grep -q . ||
    fail "release candidate was constructed under runtime-traversable releases"
  python3 - "$candidate" "$prefix/srv/gpu-monitor/dev/tmp" <<'PY'
import os, stat, sys
candidate, staging = sys.argv[1:]
candidate_stat = os.stat(candidate)
staging_stat = os.stat(staging)
mode = stat.S_IMODE(candidate_stat.st_mode)
assert mode == 0o2700, oct(mode)
assert candidate_stat.st_gid == staging_stat.st_gid, (candidate_stat.st_gid, staging_stat.st_gid)
PY
  touch "$continue"
  if ! wait "$activation_pid"; then
    cat "$tmp/activate.err" >&2
    fail "private staging activation failed"
  fi
  python3 - "$prefix/srv/gpu-monitor/dev/releases/$sha" "$prefix/srv/gpu-monitor/dev/tmp" <<'PY'
import os, stat, sys
release, staging = sys.argv[1:]
expected_gid = os.stat(staging).st_gid
for current, dirs, files in os.walk(release):
    st = os.lstat(current)
    assert st.st_gid == expected_gid, (current, st.st_gid, expected_gid)
    assert stat.S_IMODE(st.st_mode) == 0o550, (current, oct(stat.S_IMODE(st.st_mode)))
    for name in files:
        path = os.path.join(current, name)
        st = os.lstat(path)
        assert st.st_gid == expected_gid, (path, st.st_gid, expected_gid)
        if stat.S_ISREG(st.st_mode):
            assert stat.S_IMODE(st.st_mode) in (0o440, 0o550), (path, oct(stat.S_IMODE(st.st_mode)))
PY
  ! find "$prefix/srv/gpu-monitor/dev/tmp" -mindepth 1 -maxdepth 1 -name 'release-*' | grep -q . ||
    fail "successful activation left a staging candidate"
  log "candidates stage privately in tmp and publish only verified runtime-gid read-only inodes"
}

test_cross_parent_publish_restores_owner_write_on_every_platform() {
  python3 - "$ACTIVATE_SCRIPT" <<'PY'
import sys

text = open(sys.argv[1], encoding="utf-8").read()
start = text.index("publish_release() {")
end = text.index("\n}\n\nvalidate_existing_release()", start)
body = text[start:end]

assert 'if [[ "$(/usr/bin/uname -s)" == Darwin ]]' not in body, (
    "cross-parent publication must not limit the owner-write workaround to Darwin"
)
chmod_writable = body.index('chmod 0750 "$temporary"')
rename = body.index('mv "$temporary" "$release"')
chmod_immutable = body.index('chmod 0550 "$release"')
assert chmod_writable < rename < chmod_immutable, (
    "publication must restore owner write before cross-parent rename and immutability afterward"
)
PY
  log "cross-parent publication restores owner write on every supported platform"
}

run_release_metadata_verifier_failure_case() {
  local failure=$1 sha=$2 tmp prefix fakebin log_file digest python_wrapper alternate_gid=
  tmp=$(mktemp_dir "gpu-release-metadata-$failure")
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  python_wrapper="$tmp/metadata-python"
  install_fake_server_commands "$fakebin" "$log_file" pass
  if [[ "$failure" == nested-gid ]]; then
    alternate_gid=$(id -G | tr ' ' '\n' | awk -v current="$(id -g)" '$1 != current { print; exit }')
    if [[ -z "$alternate_gid" && "$(id -u)" == 0 ]]; then
      alternate_gid=$(( $(id -g) + 1 ))
    fi
    [[ -n "$alternate_gid" ]] ||
      fail "nested GID regression requires root or an alternate supplementary group"
  fi
  printf '%s\n' "$failure" > "$python_wrapper.failure"
  printf '%s\n' "$alternate_gid" > "$python_wrapper.gid"
  cat > "$python_wrapper" <<'PYWRAPPER'
#!/usr/bin/env bash
set -euo pipefail
real_python=/usr/bin/python3
if [[ "${1:-}" != - ]]; then exec "$real_python" "$@"; fi
script=$(mktemp "${TMPDIR:-/tmp}/gpu-monitor-metadata-python.XXXXXX")
trap 'rm -f "$script"' EXIT
/bin/cat > "$script"
if /usr/bin/grep -Eq 'final directory mode mismatch|published inode metadata mismatch' "$script"; then
  failure=$(cat "$0.failure")
  case "$failure" in
    verifier-failure)
      exit 91
      ;;
    nested-mode)
      chmod 0600 "$2/backend/main.py"
      ;;
    nested-gid)
      chgrp "$(cat "$0.gid")" "$2/backend/main.py"
      ;;
  esac
fi
exec "$real_python" "$script" "${@:2}"
PYWRAPPER
  chmod +x "$python_wrapper"
  mkdir -p "$tmp/artifact"
  make_release_artifact "$tmp/artifact" "$sha" "$failure"
  digest=$(cat "$tmp/artifact/digest")
  run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/artifact/gpu-monitor-$sha.tar.gz"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null \
    "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin GPU_MONITOR_INTERNAL_PYTHON=$python_wrapper" \
    >"$tmp/activate.out" 2>"$tmp/activate.err"; then
    fail "$failure metadata verifier failure was masked"
  fi
  [[ ! -e "$prefix/srv/gpu-monitor/dev/releases/$sha" ]] ||
    fail "$failure metadata mismatch published a release"
  [[ ! -e "$prefix/srv/gpu-monitor/dev/current" ]] ||
    fail "$failure metadata mismatch changed current"
  ! find "$prefix/srv/gpu-monitor/dev/tmp" -mindepth 1 -maxdepth 1 -name 'release-*' | grep -q . ||
    fail "$failure metadata mismatch left a staging candidate"
  trap - RETURN
  chmod -R u+w "$tmp" 2>/dev/null || true
  rm -rf "$tmp"
}

test_metadata_verifier_python_failure_prevents_publish() {
  run_release_metadata_verifier_failure_case verifier-failure a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1
  log "metadata verifier Python failure is propagated before fsync and publish"
}

test_nested_wrong_mode_prevents_publish() {
  run_release_metadata_verifier_failure_case nested-mode b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2
  log "nested wrong final mode prevents release publication"
}

test_nested_wrong_gid_prevents_publish() {
  run_release_metadata_verifier_failure_case nested-gid c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3
  log "nested wrong runtime GID prevents release publication"
}

test_retention_uses_latest_success_recency() {
  local tmp prefix fakebin log_file sha digest label
  tmp=$(mktemp_dir gpu-release-retention-recency)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass
  for label in A B C D A; do
    case "$label" in
      A) sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
      B) sha=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
      C) sha=cccccccccccccccccccccccccccccccccccccccc ;;
      D) sha=dddddddddddddddddddddddddddddddddddddddd ;;
    esac
    if [[ ! -d "$tmp/$label" ]]; then
      mkdir -p "$tmp/$label"; make_release_artifact "$tmp/$label" "$sha" "$label"
    fi
    digest=$(cat "$tmp/$label/digest")
    if [[ ! -d "$prefix/srv/gpu-monitor/dev/releases/$sha" ]]; then
      run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/$label/gpu-monitor-$sha.tar.gz"
    fi
    run_forced_command "$prefix" "activate dev $sha $digest" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin"
  done
  [[ ! -d "$prefix/srv/gpu-monitor/dev/releases/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" ]] ||
    fail "latest-success retention kept stale B"
  for sha in cccccccccccccccccccccccccccccccccccccccc dddddddddddddddddddddddddddddddddddddddd aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; do
    [[ -d "$prefix/srv/gpu-monitor/dev/releases/$sha" ]] || fail "latest-success retention removed $sha"
  done
  log "retention sequence A,B,C,D,A keeps C,D,A plus active pointer invariants"
}

test_installer_separate_users_prefix_upgrade_and_idempotency() {
  local tmp prefix key livekey dev_auth live_auth before after hostile_key
  tmp=$(mktemp_dir gpu-release-installer-isolation)
  tmp=$(cd "$tmp" && pwd -P)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/install"
  key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDEVKEYONLY00000000000000000000000000000000000 dev@example'
  livekey='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILIVEKEYONLY000000000000000000000000000000000 live@example'
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$key" > "$tmp/first.out"
  dev_auth="$prefix/home/gpu-deploy-dev/.ssh/authorized_keys"
  live_auth="$prefix/home/gpu-deploy-live/.ssh/authorized_keys"
  [[ -f "$dev_auth" ]] || fail "installer omitted dev identity key file"
  [[ ! -e "$live_auth" ]] || fail "installer created live key without explicit input"
  assert_mode "$prefix/home/gpu-deploy-dev/.ssh" 0755
  assert_mode "$dev_auth" 0644
  grep -Fq 'command="/usr/local/libexec/gpu-monitor-deploy-command dev"' "$dev_auth" ||
    fail "dev identity does not force the dev-only command"
  ! grep -Eq -- '--test-mode|environment=| live"' "$dev_auth" || fail "dev forced key can widen its environment"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$key" --live-public-key "$livekey" > "$tmp/live.out"
  grep -Fq 'command="/usr/local/libexec/gpu-monitor-deploy-command live"' "$live_auth" ||
    fail "live identity does not force the live-only command"
  cp "$live_auth" "$tmp/live-before"
  printf '\nOPERATOR_SECRET=preserve-me\nPORT=9999\n' >> "$prefix/etc/gpu-monitor/dev.env"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$key" > "$tmp/upgrade.out"
  cmp "$tmp/live-before" "$live_auth" || fail "omitted live key did not preserve existing live authorization"
  grep -Fxq 'OPERATOR_SECRET=preserve-me' "$prefix/etc/gpu-monitor/dev.env" || fail "upgrade destroyed operator secret"
  grep -Fxq 'PORT=5174' "$prefix/etc/gpu-monitor/dev.env" || fail "upgrade failed to enforce reserved frontend port"
  grep -Fxq 'GPU_MONITOR_BACKEND_PORT=8101' "$prefix/etc/gpu-monitor/dev.env" || fail "upgrade failed to merge missing required port"
  before=$(find "$prefix" -type f -exec shasum -a 256 {} \; -exec stat -f '%Lp %N' {} \; | LC_ALL=C sort)
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$key" > "$tmp/repeat.out"
  after=$(find "$prefix" -type f -exec shasum -a 256 {} \; -exec stat -f '%Lp %N' {} \; | LC_ALL=C sort)
  [[ "$before" == "$after" ]] || fail "repeat install was not byte/mode idempotent"
  [[ -d "$prefix/var/lock/gpu-monitor/dev" && -d "$prefix/var/lock/gpu-monitor/live" ]] ||
    fail "installer omitted isolated lock paths"
  [[ -f "$prefix/etc/sudoers.d/gpu-monitor-deploy-dev" && -f "$prefix/etc/sudoers.d/gpu-monitor-deploy-live" ]] ||
    fail "installer omitted restart authorization"
  ! grep -Fq 'deploy_user" /usr/sbin/nologin' "$INSTALLER_SCRIPT" || fail "installer creates nologin deploy SSH accounts"
  grep -Fq 'runtime_user" "/var/lib/gpu-monitor/$environment" /usr/sbin/nologin' "$INSTALLER_SCRIPT" || fail "installer does not make runtime users non-login"
  grep -Fq '/usr/sbin/usermod --password "$password_hash" "$deploy_user"' "$INSTALLER_SCRIPT" ||
    fail "installer does not assign an unknown random password hash to public-key deploy users"
  grep -Fq '/usr/sbin/usermod -L "$runtime_user"' "$INSTALLER_SCRIPT" ||
    fail "installer does not password-lock non-login runtime users"
  grep -Fq 'getent passwd "$user"' "$INSTALLER_SCRIPT" || fail "installer does not validate existing deploy users"
  grep -Fq 'ensure_deploy_and_runtime_users' "$INSTALLER_SCRIPT" || fail "installer does not validate existing runtime users"
  grep -Fq 'validate_identity_separation' "$INSTALLER_SCRIPT" || fail "installer does not validate deploy/runtime UID/GID separation"
  grep -Fq 'GPU_MONITOR_SHARED_DIR=/var/lib/gpu-monitor/dev' "$prefix/etc/gpu-monitor/dev.env" || fail "installer omitted dev mutable shared dir"
  grep -Fq 'GPU_MONITOR_SHARED_DIR=/var/lib/gpu-monitor/live' "$prefix/etc/gpu-monitor/live.env" || fail "installer omitted live mutable shared dir"
  grep -Fq 'visudo -cf' "$INSTALLER_SCRIPT" || fail "installer does not validate sudoers before install"
  grep -Fq 'daemon-reload' "$INSTALLER_SCRIPT" || fail "installer does not reload systemd after unit install"
  ! grep -Eq 'systemctl .*\b(start|enable)\b' "$INSTALLER_SCRIPT" || fail "installer starts or enables services"
  ! grep -Fq -- '--test-mode-force-non-root' "$INSTALLER_SCRIPT" || fail "installer ships a root-gate bypass"

  for invalid in / /tmp/.. relative $'/tmp/new\nline'; do
    if "$INSTALLER_SCRIPT" --dry-run --prefix "$invalid" --dev-public-key "$key" > "$tmp/invalid.out" 2> "$tmp/invalid.err"; then
      fail "installer accepted non-canonical/unsafe prefix: $invalid"
    fi
  done
  ln -s / "$tmp/root-link"
  if "$INSTALLER_SCRIPT" --dry-run --prefix "$tmp/root-link" --dev-public-key "$key"; then
    fail "installer accepted symlink-to-root prefix"
  fi
  if [[ "$(id -u)" -ne 0 ]] && "$INSTALLER_SCRIPT" --prefix "$tmp/non-root" --dev-public-key "$key"; then
    fail "non-dry-run install did not require root"
  fi
  hostile_key='environment="PREFIX=/tmp/widened" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHOSTILE hostile@example'
  if "$INSTALLER_SCRIPT" --dry-run --prefix "$tmp/hostile" --dev-public-key "$hostile_key"; then
    fail "installer accepted key options"
  fi
  log "installer provisions isolated operable identities and upgrades idempotently without bypasses"
}

test_installer_runtime_traversal_ports_and_key_material_contract() {
  local tmp prefix dev_blob live_blob dev_key live_key dev_env live_env expected
  tmp=$(mktemp_dir gpu-release-installer-final-gate)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/install"
  dev_blob=$(printf 'dev-key-material' | base64 | tr -d '\n')
  live_blob=$(printf 'live-key-material' | base64 | tr -d '\n')
  dev_key="ssh-ed25519 $dev_blob first dev comment"
  live_key="ssh-ed25519 $live_blob live comment"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$dev_key" --live-public-key "$live_key" >"$tmp/install.out"

  assert_mode "$prefix/srv/gpu-monitor/dev" 0750
  assert_mode "$prefix/srv/gpu-monitor/dev/releases" 2750
  assert_mode "$prefix/srv/gpu-monitor/dev/generations" 2750
  assert_mode "$prefix/srv/gpu-monitor/dev/incoming" 0700
  assert_mode "$prefix/srv/gpu-monitor/dev/tmp" 2700
  assert_mode "$prefix/var/lock/gpu-monitor/dev" 0700
  grep -Fq 'chown "$deploy_user:$runtime_user" "$env_root" "$env_root/releases" "$env_root/generations"' "$INSTALLER_SCRIPT" ||
    fail "installer does not assign runtime-group traversal only to the release path"
  grep -Fq 'chown -R "$deploy_user:$runtime_user" "$env_root/releases" "$env_root/generations" "$env_root/tmp"' "$INSTALLER_SCRIPT" ||
    fail "installer does not deliberately preserve the runtime gid through private tmp staging"
  grep -Fq 'chown -R "$deploy_user:$deploy_user" "$env_root/incoming" "$lock_root"' "$INSTALLER_SCRIPT" ||
    fail "installer does not keep incoming and locks deploy-only"
  grep -Fq 'destination.mkdir(parents=True, mode=0o2700)' "$ACTIVATE_SCRIPT" ||
    fail "release staging root does not preserve private setgid semantics"

  dev_env="$prefix/etc/gpu-monitor/dev.env"
  cat > "$dev_env" <<'ENV'
# operator comment stays exactly here
SECRET_TOKEN=alpha=beta
GPU_MONITOR_BACKEND_PORT=9999
UNRELATED_PORT=1234
GPU_MONITOR_BACKEND_PORT=9998
PORT=9997
# another comment
PORT=9996
ENV
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$dev_key" >"$tmp/rewrite.out"
  expected="$tmp/expected-dev.env"
  cat > "$expected" <<'ENV'
# operator comment stays exactly here
SECRET_TOKEN=alpha=beta
GPU_MONITOR_BACKEND_PORT=8101
UNRELATED_PORT=1234
PORT=5174
# another comment
GPU_MONITOR_SHARED_DIR=/var/lib/gpu-monitor/dev
ENV
  cmp "$expected" "$dev_env" || fail "reserved port rewrite changed unrelated bytes or retained duplicates"
  [[ "$(grep -c '^GPU_MONITOR_BACKEND_PORT=' "$dev_env")" == 1 ]] || fail "dev backend port was not deduplicated"
  [[ "$(grep -c '^PORT=' "$dev_env")" == 1 ]] || fail "dev frontend port was not deduplicated"

  live_env="$prefix/etc/gpu-monitor/live.env"
  cat > "$live_env" <<'ENV'
# live comment
LIVE_SECRET=preserve-this
GPU_MONITOR_BRIDGE_PORT=7000
GPU_MONITOR_BACKEND_PORT=7001
PORT=7002
GPU_MONITOR_BRIDGE_PORT=7003
GPU_MONITOR_BACKEND_PORT=7004
PORT=7005
ENV
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$dev_key" >"$tmp/live-rewrite.out"
  expected="$tmp/expected-live.env"
  cat > "$expected" <<'ENV'
# live comment
LIVE_SECRET=preserve-this
GPU_MONITOR_BRIDGE_PORT=8000
GPU_MONITOR_BACKEND_PORT=8001
PORT=5173
GPU_MONITOR_SHARED_DIR=/var/lib/gpu-monitor/live
ENV
  cmp "$expected" "$live_env" || fail "live reserved port rewrite changed unrelated bytes or retained duplicates"
  [[ "$(grep -c '^GPU_MONITOR_BACKEND_PORT=' "$live_env")" == 1 ]] || fail "live backend port was not deduplicated"
  [[ "$(grep -c '^GPU_MONITOR_BRIDGE_PORT=' "$live_env")" == 1 ]] || fail "live bridge port was not deduplicated"
  [[ "$(grep -c '^PORT=' "$live_env")" == 1 ]] || fail "live frontend port was not deduplicated"

  grep -Fxq "restrict,command=\"/usr/local/libexec/gpu-monitor-deploy-command dev\" ssh-ed25519 $dev_blob" \
    "$prefix/home/gpu-deploy-dev/.ssh/authorized_keys" ||
    fail "authorized key was not normalized to key type and base64 blob"
  if "$INSTALLER_SCRIPT" --dry-run --prefix "$tmp/same-material" \
    --dev-public-key "ssh-ed25519 $dev_blob dev comment" \
    --live-public-key "ssh-ed25519 $dev_blob different live comment" >"$tmp/same.out" 2>"$tmp/same.err"; then
    fail "installer accepted identical dev/live key material with different comments"
  fi
  log "installer enforces runtime traversal, exact reserved ports, and normalized key separation"
}

test_installer_reconciles_published_modes_without_exposing_hidden_candidates() {
  local tmp prefix key release generation hidden
  tmp=$(mktemp_dir gpu-release-installer-reconcile)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/install"
  key='ssh-ed25519 AAAARECONCILEKEY0000000000000000000000000000000000000 reconcile'
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$key" >"$tmp/first.out"
  release="$prefix/srv/gpu-monitor/dev/releases/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  generation="$prefix/srv/gpu-monitor/dev/generations/gen-existing"
  hidden="$prefix/srv/gpu-monitor/dev/releases/.release-stale"
  mkdir -p "$release/sub" "$generation" "$hidden/sub"
  printf 'published\n' >"$release/sub/file"
  printf 'hidden\n' >"$hidden/sub/file"
  chmod 0770 "$release" "$release/sub" "$generation" "$hidden" "$hidden/sub"
  chmod 0660 "$release/sub/file" "$hidden/sub/file"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$key" >"$tmp/reinstall.out"
  assert_mode "$prefix/srv/gpu-monitor/dev/releases" 2750
  assert_mode "$prefix/srv/gpu-monitor/dev/generations" 2750
  assert_mode "$release" 0550
  assert_mode "$release/sub" 0550
  assert_mode "$release/sub/file" 0440
  assert_mode "$generation" 0550
  assert_mode "$hidden" 0700
  assert_mode "$hidden/sub" 0700
  assert_mode "$hidden/sub/file" 0600
  ! grep -Fq 'find "$env_root/releases" "$env_root/generations" -type d -exec chmod 2750' "$INSTALLER_SCRIPT" ||
    fail "installer still recursively makes immutable release/generation directories writable"
  log "reinstall preserves writable parents while reconciling published and hidden trees safely"
}

test_installer_materializes_and_validates_managed_node_runtime() {
  local tmp prefix key node_prefix old_node_prefix first_target
  tmp=$(mktemp_dir gpu-release-installer-node)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/install"
  key='ssh-ed25519 AAAANODERUNTIMEKEY0000000000000000000000000000000000000 node'
  node_prefix="$tmp/node-v24"
  old_node_prefix="$tmp/node-v12"

  mkdir -p "$node_prefix/bin" "$node_prefix/lib/node_modules/npm/bin"
  cat > "$node_prefix/bin/node" <<'NODE'
#!/usr/bin/env sh
if [ "${1:-}" = --version ]; then printf 'v24.14.0\n'; exit 0; fi
exit 0
NODE
  chmod 0755 "$node_prefix/bin/node"
  printf 'console.log("npm fixture")\n' > "$node_prefix/lib/node_modules/npm/bin/npm-cli.js"
  ln -s ../lib/node_modules/npm/bin/npm-cli.js "$node_prefix/bin/npm"

  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" \
    --dev-public-key "$key" --node-prefix "$node_prefix" >"$tmp/install.out"
  [[ -x "$prefix/opt/gpu-monitor/node/bin/node" ]] ||
    fail "installer did not materialize the managed Node executable"
  [[ -f "$prefix/opt/gpu-monitor/node/lib/node_modules/npm/bin/npm-cli.js" ]] ||
    fail "installer did not materialize the coherent npm runtime"
  [[ -L "$prefix/opt/gpu-monitor/node/bin/npm" ]] ||
    fail "installer did not preserve the relative npm launcher symlink"
  first_target=$(readlink "$prefix/opt/gpu-monitor/node")
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" \
    --dev-public-key "$key" --node-prefix "$node_prefix" >"$tmp/reinstall.out"
  [[ -L "$prefix/opt/gpu-monitor/node" ]] ||
    fail "managed Node pointer stopped being a symlink after idempotent reinstall"
  [[ "$(readlink "$prefix/opt/gpu-monitor/node")" == "$first_target" ]] ||
    fail "idempotent reinstall changed the managed Node runtime target"
  ! find "$prefix/opt/gpu-monitor/node-runtimes" -name '.node.next.*' | grep -q . ||
    fail "idempotent reinstall moved the next-pointer inside the active runtime"
  grep -Fq 'node_command=/opt/gpu-monitor/node/bin/node' "$ACTIVATE_SCRIPT" ||
    fail "production activation does not use the managed Node executable"
  grep -Fq 'npm_cli=/opt/gpu-monitor/node/lib/node_modules/npm/bin/npm-cli.js' "$ACTIVATE_SCRIPT" ||
    fail "production activation does not use npm from the managed Node prefix"

  mkdir -p "$old_node_prefix/bin" "$old_node_prefix/lib/node_modules/npm/bin"
  cat > "$old_node_prefix/bin/node" <<'NODE'
#!/usr/bin/env sh
if [ "${1:-}" = --version ]; then printf 'v12.22.9\n'; exit 0; fi
exit 0
NODE
  chmod 0755 "$old_node_prefix/bin/node"
  printf 'console.log("old npm fixture")\n' > "$old_node_prefix/lib/node_modules/npm/bin/npm-cli.js"
  ln -s ../lib/node_modules/npm/bin/npm-cli.js "$old_node_prefix/bin/npm"
  if "$INSTALLER_SCRIPT" --dry-run --prefix "$tmp/old-install" \
    --dev-public-key "$key" --node-prefix "$old_node_prefix" >"$tmp/old.out" 2>"$tmp/old.err"; then
    fail "installer accepted the incident Node v12 runtime"
  fi
  grep -Eiq 'node.*(18|version|old|minimum)' "$tmp/old.err" ||
    fail "old Node rejection did not explain the minimum runtime requirement"
  log "installer materializes a coherent managed Node runtime and rejects Node 12"
}

test_omitted_live_key_blocks_conflicting_dev_rotation_before_mutation() {
  local tmp prefix dev_blob live_blob dev_key live_key dev_auth live_auth
  tmp=$(mktemp_dir gpu-release-key-rotation)
  trap 'rm -rf "$tmp"' RETURN
  prefix="$tmp/install"
  dev_blob=$(printf 'rotation-dev' | base64 | tr -d '\n')
  live_blob=$(printf 'rotation-live' | base64 | tr -d '\n')
  dev_key="ssh-ed25519 $dev_blob old dev"
  live_key="ssh-ed25519 $live_blob installed live"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" --dev-public-key "$dev_key" --live-public-key "$live_key" >"$tmp/first.out"
  dev_auth="$prefix/home/gpu-deploy-dev/.ssh/authorized_keys"
  live_auth="$prefix/home/gpu-deploy-live/.ssh/authorized_keys"
  cp "$dev_auth" "$tmp/dev-before"
  cp "$live_auth" "$tmp/live-before"
  if "$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" \
    --dev-public-key "ssh-ed25519 $live_blob proposed dev with different comment" \
    >"$tmp/rotate.out" 2>"$tmp/rotate.err"; then
    fail "omitted live key allowed dev rotation to installed live key material"
  fi
  cmp "$tmp/dev-before" "$dev_auth" || fail "conflicting dev rotation mutated dev authorization before rejection"
  cmp "$tmp/live-before" "$live_auth" || fail "conflicting dev rotation mutated live authorization"
  log "omitted live authorization participates in normalized key separation before mutation"
}

test_archive_validation_retention_status_and_installer() {
  local tmp prefix fakebin log_file sha digest
  tmp=$(mktemp_dir gpu-release-security-retention)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"; fakebin="$tmp/fakebin"; log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" pass

  sha=dddddddddddddddddddddddddddddddddddddddd
  mkdir -p "$tmp/bad"
  python3 - "$tmp/bad/gpu-monitor-$sha.tar.gz" <<'PYBAD'
import io, tarfile, sys
with tarfile.open(sys.argv[1], "w:gz") as tar:
    data = b"escape"
    info = tarfile.TarInfo("../escape")
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
PYBAD
  digest=$(sha256_file "$tmp/bad/gpu-monitor-$sha.tar.gz" | awk '{print $1}')
  run_forced_command "$prefix" "upload dev $sha $digest" "$tmp/bad/gpu-monitor-$sha.tar.gz"
  if run_forced_command "$prefix" "activate dev $sha $digest" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/badact.out" 2>/"$tmp/badact.err"; then
    fail "activation accepted parent-traversal archive"
  fi

  local i relsha reldigest
  for i in 1 2 3 4; do
    relsha=$(printf '%040d' "$i")
    mkdir -p "$tmp/rel$i"
    make_release_artifact "$tmp/rel$i" "$relsha" "rel$i"
    reldigest=$(cat "$tmp/rel$i/digest")
    run_forced_command "$prefix" "upload dev $relsha $reldigest" "$tmp/rel$i/gpu-monitor-$relsha.tar.gz"
    run_forced_command "$prefix" "activate dev $relsha $reldigest" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/act$i.out" 2>/"$tmp/act$i.err"
  done
  [[ ! -d "$prefix/srv/gpu-monitor/dev/releases/0000000000000000000000000000000000000001" ]] || fail "old successful release was not pruned"
  [[ -d "$prefix/srv/gpu-monitor/dev/releases/0000000000000000000000000000000000000003" ]] || fail "previous release was incorrectly pruned"
  [[ -d "$prefix/srv/gpu-monitor/dev/releases/0000000000000000000000000000000000000004" ]] || fail "current release was incorrectly pruned"
  run_forced_command "$prefix" "status dev" >/"$tmp/status.out"
  grep -q '"environment":"dev"' "$tmp/status.out" || fail "status did not emit environment JSON"

  log "archive validation, retention, and status contract are enforced"
}

run_test test_server_scripts_exist_before_security_tests
run_test test_forced_command_rejects_open_grammar_and_env_crossing
run_test test_production_mode_scrubs_hostile_environment_before_dispatch
run_test test_production_activator_rejects_mismatched_caller_before_root_mutation
run_test test_upload_is_bounded_digest_verified_and_cleans_failures
run_test test_activation_dev_live_boundaries_pointers_units_and_rollback
run_test test_first_activation_failure_restores_absent_pointer_state
run_test test_health_test_overrides_are_positive_and_bounded
run_test test_systemd_units_match_real_runtime_entrypoints
run_test test_state_appends_use_crash_durable_writer
run_test test_directory_mutations_are_fsynced
run_test test_isolated_identities_broker_and_descriptor_extraction_contract
run_test test_restart_broker_exact_unit_allowlist
run_test test_transaction_restores_both_pointers_for_restart_health_and_manual_failures
run_test test_recovery_pointer_and_fsync_failures_record_rollback_failed
run_test test_dependency_install_requires_explicit_trusted_timeout
run_test test_dependency_venv_timeout_failure_is_not_published
run_test test_dependency_pip_timeout_failure_is_not_published
run_test test_dependency_npm_timeout_failure_is_not_published
run_test test_dependency_missing_venv_interpreter_is_not_published
run_test test_dependency_missing_frontend_runtime_is_not_published
run_test test_incoming_content_addressing_quotas_and_success_cleanup
run_test test_incoming_artifact_open_rejects_symlink_and_fifo_without_hanging
run_test test_archive_rejects_all_nonregular_types_conflicts_and_limit_plus_one
run_test test_generation_pointer_model_and_failed_candidate_cleanup
run_test test_candidate_stages_privately_in_tmp_and_publishes_verified_runtime_gid
run_test test_cross_parent_publish_restores_owner_write_on_every_platform
run_test test_metadata_verifier_python_failure_prevents_publish
run_test test_nested_wrong_mode_prevents_publish
run_test test_nested_wrong_gid_prevents_publish
run_test test_retention_uses_latest_success_recency
run_test test_installer_separate_users_prefix_upgrade_and_idempotency
run_test test_installer_runtime_traversal_ports_and_key_material_contract
run_test test_installer_reconciles_published_modes_without_exposing_hidden_candidates
run_test test_installer_materializes_and_validates_managed_node_runtime
run_test test_omitted_live_key_blocks_conflicting_dev_rotation_before_mutation
run_test test_archive_validation_retention_status_and_installer
