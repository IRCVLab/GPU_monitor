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
  git -C "$fixture" add -f apps/gpu-monitor/deploy \
    apps/gpu-monitor/backend apps/gpu-monitor/frontend Makefile
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
  trap 'rm -rf "$tmp"' RETURN
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
  trap 'rm -rf "$tmp"' RETURN
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

test_build_outputs_contract() {
  local tmp repo out1 out2 sha artifact manifest list1 list2 checksum
  tmp=$(mktemp_dir gpu-release-contract)
  trap 'rm -rf "$tmp"' RETURN
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
  trap 'rm -rf "$tmp"' RETURN
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
test_build_outputs_contract
test_failed_build_leaves_no_partial_outputs_and_works_from_any_cwd
