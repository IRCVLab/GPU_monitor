#!/usr/bin/env bash
set -euo pipefail

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $# -ge 2 && $# -le 4 ]] || fail "usage: activate-release.sh <activate|rollback|status> <dev|live> [sha] [sha256]"
action=$1
env_name=$2
sha=${3:-}
digest=${4:-}
case "$action:$#" in
  status:2|rollback:2|activate:4|activate-inner:4|rollback-inner:2) ;;
  *) fail "usage: activate-release.sh <activate|rollback|status> <dev|live> [sha] [sha256]" ;;
esac
case "$action" in activate|rollback|status|activate-inner|rollback-inner) ;; *) fail "invalid action" ;; esac
case "$env_name" in dev|live) ;; *) fail "invalid environment" ;; esac
if [[ "$action" == activate ]]; then
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail "invalid sha"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "invalid sha256"
fi

PREFIX=${PREFIX:-}
PATH="${GPU_MONITOR_TEST_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
export PATH
INTERNAL_PYTHON="${GPU_MONITOR_INTERNAL_PYTHON:-/usr/bin/python3}"
[[ -x "$INTERNAL_PYTHON" ]] || INTERNAL_PYTHON=$(command -v python3)
base="${PREFIX}/srv/gpu-monitor/${env_name}"
releases="$base/releases"
incoming="$base/incoming"
locks="${PREFIX}/var/lock/gpu-monitor"
state="$base/deployments.jsonl"
mkdir -p "$releases" "$incoming" "$locks" "$base/tmp"
lock_file="$locks/${env_name}.lock"
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

json_line() {
  local status=$1 release_sha=${2:-} release_digest=${3:-} message=${4:-}
  "$INTERNAL_PYTHON" - "$env_name" "$status" "$release_sha" "$release_digest" "$message" <<'PY'
import json, sys, time
print(json.dumps({
    "ts": int(time.time()),
    "environment": sys.argv[1],
    "status": sys.argv[2],
    "sha": sys.argv[3],
    "sha256": sys.argv[4],
    "message": sys.argv[5],
}, sort_keys=True, separators=(",", ":")))
PY
}

atomic_link() {
  local target=$1 link=$2 tmp_link
  tmp_link="${link}.tmp.$$"
  rm -f "$tmp_link"
  ln -s "$target" "$tmp_link"
  "$INTERNAL_PYTHON" - "$tmp_link" "$link" <<'PY'
import os, sys
os.replace(sys.argv[1], sys.argv[2])
PY
}

current_target() { [[ -L "$base/current" ]] && readlink "$base/current" || true; }
previous_target() { [[ -L "$base/previous" ]] && readlink "$base/previous" || true; }

restart_units() {
  local e=$1
  systemctl restart "gpu-monitor-backend@${e}.service" "gpu-monitor-frontend@${e}.service"
  if [[ "$e" == live ]]; then
    systemctl restart "gpu-monitor-bridge@${e}.service"
  fi
}

validate_and_extract() {
  local artifact=$1 dest=$2 expected_sha=$3 expected_digest=$4
  "$INTERNAL_PYTHON" - "$artifact" "$dest" "$expected_sha" "$expected_digest" <<'PY'
import hashlib, json, os, shutil, stat, sys, tarfile
from pathlib import Path, PurePosixPath
artifact = Path(sys.argv[1])
dest = Path(sys.argv[2])
sha = sys.argv[3]
expected = sys.argv[4]
MAX_FILES = int(os.environ.get("GPU_MONITOR_MAX_ARCHIVE_FILES", "10000"))
MAX_EXPANDED = int(os.environ.get("GPU_MONITOR_MAX_EXPANDED_BYTES", str(2 * 1024 * 1024 * 1024)))

def fail(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(1)

digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
if digest != expected:
    fail("artifact digest mismatch")
if dest.exists():
    fail("temporary extraction destination already exists")
dest.mkdir(parents=True)
root = dest / "root"
root.mkdir()
count = 0
expanded = 0
with tarfile.open(artifact, "r:gz") as tar:
    members = tar.getmembers()
    for m in members:
        count += 1
        if count > MAX_FILES:
            fail("archive contains too many entries")
        name = m.name
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts:
            fail(f"unsafe archive path: {name}")
        if not p.parts or p.parts[0] != "gpu-monitor":
            fail(f"invalid archive root: {name}")
        mode = m.mode or 0
        if mode & (stat.S_ISUID | stat.S_ISGID):
            fail(f"setuid/setgid archive entry rejected: {name}")
        if m.issym() or m.islnk() or m.isdev() or m.isfifo():
            fail(f"unsafe archive entry type rejected: {name}")
        if m.isfile():
            expanded += m.size
            if expanded > MAX_EXPANDED:
                fail("archive expanded size exceeds bound")
    tar.extractall(root, members=members)
app_root = root / "gpu-monitor"
if not app_root.is_dir():
    fail("archive missing gpu-monitor root")
required = ["backend/main.py", "backend/requirements.txt", "frontend/package.json", "frontend/package-lock.json", "frontend/build/index.js"]
for rel in required:
    if not (app_root / rel).is_file():
        fail(f"archive missing required runtime file: {rel}")
for child in app_root.iterdir():
    shutil.move(str(child), str(dest / child.name))
shutil.rmtree(root)
manifest = {
    "application": "gpu-monitor",
    "artifact": f"gpu-monitor-{sha}.tar.gz",
    "git_sha": sha,
    "schema": 1,
    "sha256": digest,
}
(dest / "release-manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
}

install_dependencies() {
  local dir=$1
  python3 -m venv "$dir/.venv"
  if [[ -x "$dir/.venv/bin/python" ]]; then
    "$dir/.venv/bin/python" -m pip install --requirement "$dir/backend/requirements.txt"
  fi
  (cd "$dir/frontend" && npm ci --omit=dev)
}

retain_successful_releases() {
  local keep current previous name rel preserve kept
  current=$(current_target)
  previous=$(previous_target)
  keep=$("$INTERNAL_PYTHON" - "$state" <<'PY'
import json, sys
seen=[]
try:
    lines=open(sys.argv[1], encoding='utf-8')
except FileNotFoundError:
    lines=[]
for line in lines:
    try: data=json.loads(line)
    except Exception: continue
    if data.get('status') == 'success' and data.get('sha') and data['sha'] not in seen:
        seen.append(data['sha'])
print('\n'.join(seen[-3:]))
PY
)
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    name="${dir##*/}"
    rel="releases/${name}"
    preserve=false
    kept=""
    [[ "$rel" == "$current" || "$rel" == "$previous" ]] && preserve=true
    while IFS= read -r kept; do [[ "$kept" == "$name" ]] && preserve=true; done <<< "$keep"
    if [[ "$preserve" == false ]]; then chmod -R u+w "$dir" 2>/dev/null || true; rm -rf "$dir"; fi
  done < <(find "$releases" -mindepth 1 -maxdepth 1 -type d | LC_ALL=C sort)
}

do_status() {
  "$INTERNAL_PYTHON" - "$env_name" "$(current_target)" "$(previous_target)" "$state" <<'PY'
import json, sys
print(json.dumps({"environment": sys.argv[1], "current": sys.argv[2], "previous": sys.argv[3], "state": sys.argv[4]}, sort_keys=True, separators=(",", ":")))
PY
}

do_rollback() {
  local cur prev
  prev=$(previous_target)
  [[ -n "$prev" && -d "$base/$prev" ]] || fail "no previous release to roll back to"
  cur=$(current_target)
  atomic_link "$cur" "$base/previous"
  atomic_link "$prev" "$base/current"
  restart_units "$env_name"
  "$script_dir/health-check.sh" "$env_name"
  json_line rollback "${prev##*/}" "" manual >> "$state"
}

do_activate() {
  local artifact release_dir tmp_dir old_current old_previous
  artifact="$incoming/$sha.tar.gz"
  [[ -f "$artifact" ]] || fail "incoming artifact is missing"
  actual=$("$INTERNAL_PYTHON" - "$artifact" <<'PY'
import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())
PY
)
  [[ "$actual" == "$digest" ]] || fail "incoming artifact digest mismatch"
  release_dir="$releases/$sha"
  old_current=$(current_target)
  old_previous=$(previous_target)
  if [[ ! -d "$release_dir" ]]; then
    tmp_dir="$base/tmp/release-${sha}.$$"
    rm -rf "$tmp_dir"
    validate_and_extract "$artifact" "$tmp_dir" "$sha" "$digest"
    install_dependencies "$tmp_dir"
    mv "$tmp_dir" "$release_dir"
    chmod -R a-w "$release_dir" || true
  fi
  if [[ -n "$old_current" && "$old_current" != "releases/$sha" ]]; then
    atomic_link "$old_current" "$base/previous"
  elif [[ -n "$old_previous" ]]; then
    atomic_link "$old_previous" "$base/previous"
  fi
  atomic_link "releases/$sha" "$base/current"
  restart_units "$env_name"
  if "$script_dir/health-check.sh" "$env_name"; then
    json_line success "$sha" "$digest" activated >> "$state"
    retain_successful_releases
  else
    if [[ -n "$old_current" ]]; then
      atomic_link "$old_current" "$base/current"
      if [[ -n "$old_previous" ]]; then atomic_link "$old_previous" "$base/previous"; else rm -f "$base/previous"; fi
      restart_units "$env_name" || true
      "$script_dir/health-check.sh" "$env_name" || true
    fi
    json_line rollback "$sha" "$digest" health_failed >> "$state"
    fail "activation health failed; rolled back"
  fi
}

case "$action" in
  status) do_status ;;
  rollback) flock "$lock_file" "$0" rollback-inner "$env_name" ;;
  activate) flock "$lock_file" "$0" activate-inner "$env_name" "$sha" "$digest" ;;
  rollback-inner) do_rollback ;;
  activate-inner) do_activate ;;
esac
