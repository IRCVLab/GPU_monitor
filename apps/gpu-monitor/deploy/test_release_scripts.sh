#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT=$(cd "$SCRIPT_DIR/../../.." && pwd)
BUILD_SCRIPT="$SOURCE_ROOT/apps/gpu-monitor/deploy/build-release.sh"

mktemp_dir() {
  mktemp -d "${TMPDIR:-/tmp}/$1.XXXXXX"
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

test_missing_builder_fails
test_rejects_dirty_source
test_rejects_invalid_and_non_head_sha
test_rejects_untracked_nonignored_sources_before_build
test_build_does_not_mutate_checkout_node_modules_or_build
test_post_temp_output_failure_cleans_tmp_outputs
test_build_outputs_contract
test_failed_build_leaves_no_partial_outputs_and_works_from_any_cwd

SERVER_DIR="$SOURCE_ROOT/apps/gpu-monitor/deploy/server"
DEPLOY_COMMAND="$SERVER_DIR/gpu-monitor-deploy-command"
ACTIVATE_SCRIPT="$SERVER_DIR/activate-release.sh"
HEALTH_SCRIPT="$SERVER_DIR/health-check.sh"
INSTALLER_SCRIPT="$SERVER_DIR/install-deployer.sh"

assert_symlink_target() {
  local link=$1 expected=$2 actual
  [[ -L "$link" ]] || fail "$link is not a symlink"
  actual=$(readlink "$link")
  [[ "$actual" == "$expected" ]] || fail "$link points to $actual, expected $expected"
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
  local fakebin=$1 log_file=$2 health_mode=${3:-pass}
  mkdir -p "$fakebin"
  cat > "$fakebin/systemctl" <<FAKE
#!/usr/bin/env bash
printf 'systemctl %s\\n' "\$*" >> '$log_file'
exit 0
FAKE
  cat > "$fakebin/curl" <<FAKE
#!/usr/bin/env bash
printf 'curl %s\\n' "\$*" >> '$log_file'
if [[ '$health_mode' == fail ]]; then exit 22; fi
exit 0
FAKE
  cat > "$fakebin/python3" <<FAKE
#!/usr/bin/env bash
printf 'python3 %s\\n' "\$*" >> '$log_file'
if [[ "\$*" == *' -m venv '* || "\$*" == *' venv '* ]]; then mkdir -p "\${@: -1}/bin"; touch "\${@: -1}/bin/python"; fi
exit 0
FAKE
  cat > "$fakebin/npm" <<FAKE
#!/usr/bin/env bash
printf 'npm %s\\n' "\$*" >> '$log_file'
exit 0
FAKE
  cat > "$fakebin/flock" <<FAKE
#!/usr/bin/env bash
printf 'flock %s\\n' "\$1" >> '$log_file'
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
  log "forced-command grammar and env authorization reject unsafe requests"
}

test_production_mode_scrubs_hostile_environment_before_dispatch() {
  local tmp fakebin marker hostile_prefix
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

  env -i \
    PATH="$fakebin" \
    PREFIX="$hostile_prefix" \
    GPU_MONITOR_TEST_PATH="$fakebin" \
    GPU_MONITOR_MAX_UPLOAD_BYTES=1 \
    GPU_MONITOR_ALLOWED_ENV=live \
    GPU_MONITOR_INTERNAL_PYTHON="$fakebin/python3" \
    SSH_ORIGINAL_COMMAND="status dev" \
    "$DEPLOY_COMMAND" dev > "$tmp/status.out"

  grep -Fq '"state":"/srv/gpu-monitor/dev/deployments.jsonl"' "$tmp/status.out" ||
    fail "production status did not use the hard-coded deployment root"
  [[ ! -e "$marker" ]] || fail "hostile inherited PATH/internal Python altered production dispatch"
  [[ ! -e "$hostile_prefix" ]] || fail "hostile inherited PREFIX altered production root"
  log "production forced-command mode scrubs hostile inherited overrides before dispatch"
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
  [[ ! -e "$prefix/srv/gpu-monitor/dev/incoming/$sha.tar.gz" ]] || fail "bad upload left incoming artifact"

  run_forced_command "$prefix" "upload dev $sha $digest" "$artifact" >/"$tmp/good.out" 2>/"$tmp/good.err"
  [[ -s "$prefix/srv/gpu-monitor/dev/incoming/$sha.tar.gz" ]] || fail "verified upload did not persist incoming artifact"

  sha=2222222222222222222222222222222222222222
  make_release_artifact "$tmp/artifact" "$sha" oversize
  artifact="$tmp/artifact/gpu-monitor-$sha.tar.gz"
  digest=$(cat "$tmp/artifact/digest")
  if run_forced_command "$prefix" "upload dev $sha $digest" "$artifact" "GPU_MONITOR_MAX_UPLOAD_BYTES=1" dev >/"$tmp/large.out" 2>/"$tmp/large.err"; then
    fail "upload accepted artifact over configured size bound"
  fi
  [[ ! -e "$prefix/srv/gpu-monitor/dev/incoming/$sha.tar.gz" ]] || fail "oversized upload left incoming artifact"

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
  run_forced_command "$prefix" "upload dev $sha1 $conflicting_digest" "$tmp/conflicting/gpu-monitor-$sha1.tar.gz"
  if run_forced_command "$prefix" "activate dev $sha1 $conflicting_digest" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/conflicting.out" 2>/"$tmp/conflicting.err"; then
    fail "activation reused an existing SHA for a conflicting artifact digest"
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
  rm -rf "$fakebin"; : > "$log_file"; install_fake_server_commands "$fakebin" "$log_file" fail
  if run_forced_command "$prefix" "activate dev $sha3 $digest3" /dev/null "GPU_MONITOR_TEST_PATH=$fakebin:/usr/bin:/bin" dev >/"$tmp/act-fail.out" 2>/"$tmp/act-fail.err"; then
    fail "activation succeeded despite failing health"
  fi
  assert_symlink_target "$prefix/srv/gpu-monitor/dev/current" "releases/$sha1"
  grep -q '"status":"rollback"' "$prefix/srv/gpu-monitor/dev/deployments.jsonl" || fail "rollback state was not recorded"
  log "activation isolates envs, uses atomic pointers/units/flocks, and rolls back on failed health"
}

test_first_activation_failure_restores_absent_pointer_state() {
  local tmp prefix fakebin log_file sha digest
  tmp=$(mktemp_dir gpu-release-first-rollback)
  trap 'chmod -R u+w "$tmp" 2>/dev/null || true; rm -rf "$tmp"' RETURN
  prefix="$tmp/prefix"
  fakebin="$tmp/fakebin"
  log_file="$tmp/commands.log"
  install_fake_server_commands "$fakebin" "$log_file" fail
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
  grep -Fq 'ExecStart=/usr/bin/node /srv/gpu-monitor/%i/current/frontend/build/index.js' "$frontend_unit" ||
    fail "frontend unit does not run the built adapter-node entrypoint"
  grep -Fq 'Environment=HOST=127.0.0.1' "$frontend_unit" ||
    fail "frontend unit does not bind adapter-node to loopback"
  log "systemd units match the real backend modules and built Svelte adapter-node runtime"
}

test_state_appends_use_crash_durable_writer() {
  grep -Fq 'os.fsync' "$ACTIVATE_SCRIPT" || fail "deployment state writer does not fsync JSONL appends"
  if grep -Eq 'json_line .*>>.*state' "$ACTIVATE_SCRIPT"; then
    fail "deployment state still uses non-durable shell redirection"
  fi
  log "deployment JSONL state appends use the crash-durable writer"
}

test_archive_validation_retention_status_and_installer() {
  local tmp prefix fakebin log_file sha digest key livekey install_prefix
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

  key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDEVKEYONLY00000000000000000000000000000000000 dev@example'
  livekey='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILIVEKEYONLY000000000000000000000000000000000 live@example'
  install_prefix="$tmp/install"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$install_prefix" --dev-public-key "$key" >/"$tmp/install-dev.out"
  [[ -f "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" ]] || fail "installer did not create authorized_keys in prefix"
  grep -Fq 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command dev"' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer did not write dev forced command restriction"
  ! grep -Fq -- '--test-mode' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer exposed test mode through authorized_keys"
  ! grep -Fq 'environment=' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer authorized_keys permits environment injection"
  grep -Fq 'DEVKEYONLY' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer did not install dev key"
  ! grep -Fq 'LIVEKEYONLY' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer installed live key without explicit live key input"
  "$INSTALLER_SCRIPT" --dry-run --prefix "$install_prefix" --dev-public-key "$key" --live-public-key "$livekey" >/"$tmp/install-live.out"
  grep -Fq 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command live"' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer did not write live forced command restriction"
  grep -Fq 'LIVEKEYONLY' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" || fail "installer did not install explicit live key"
  [[ "$(stat -f '%Lp' "$install_prefix/home/gpu-deploy/.ssh" 2>/dev/null || stat -c '%a' "$install_prefix/home/gpu-deploy/.ssh")" == 700 ]] ||
    fail "installer did not set .ssh mode 0700"
  [[ "$(stat -f '%Lp' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys" 2>/dev/null || stat -c '%a' "$install_prefix/home/gpu-deploy/.ssh/authorized_keys")" == 600 ]] ||
    fail "installer did not set authorized_keys mode 0600"
  grep -Fxq 'GPU_MONITOR_BACKEND_PORT=8101' "$install_prefix/etc/gpu-monitor/dev.env" ||
    fail "installer did not configure dev backend port"
  grep -Fxq 'PORT=5174' "$install_prefix/etc/gpu-monitor/dev.env" ||
    fail "installer did not configure dev frontend port"
  grep -Fxq 'GPU_MONITOR_BACKEND_PORT=8001' "$install_prefix/etc/gpu-monitor/live.env" ||
    fail "installer did not configure live backend port"
  grep -Fxq 'GPU_MONITOR_BRIDGE_PORT=8000' "$install_prefix/etc/gpu-monitor/live.env" ||
    fail "installer did not configure live bridge port"
  grep -Fxq 'PORT=5173' "$install_prefix/etc/gpu-monitor/live.env" ||
    fail "installer did not configure live frontend port"
  if "$INSTALLER_SCRIPT" --test-mode-force-non-root --prefix "$tmp/non-root-install" --dev-public-key "$key" > "$tmp/non-root.out" 2> "$tmp/non-root.err"; then
    fail "non-dry-run prefix install bypassed the root requirement"
  fi
  [[ ! -e "$tmp/non-root-install" ]] || fail "failed non-root install wrote files before the root gate"
  hostile_key='environment="PREFIX=/tmp/widened" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHOSTILE hostile@example'
  if "$INSTALLER_SCRIPT" --dry-run --prefix "$tmp/hostile-key-install" --dev-public-key "$hostile_key" > "$tmp/hostile-key.out" 2> "$tmp/hostile-key.err"; then
    fail "installer accepted a public-key input containing authorized_keys options"
  fi
  ! grep -Eiq 'systemctl (enable|start)' "$tmp/install-dev.out" "$tmp/install-live.out" || fail "installer attempted to start/enable services"
  log "archive validation, retention, status, and installer contract are enforced"
}

test_server_scripts_exist_before_security_tests
test_forced_command_rejects_open_grammar_and_env_crossing
test_production_mode_scrubs_hostile_environment_before_dispatch
test_upload_is_bounded_digest_verified_and_cleans_failures
test_activation_dev_live_boundaries_pointers_units_and_rollback
test_first_activation_failure_restores_absent_pointer_state
test_health_test_overrides_are_positive_and_bounded
test_systemd_units_match_real_runtime_entrypoints
test_state_appends_use_crash_durable_writer
test_archive_validation_retention_status_and_installer
