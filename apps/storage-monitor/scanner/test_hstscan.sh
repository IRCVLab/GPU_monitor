#!/usr/bin/env bash
#
# Correctness test for hstscan.
#
# Builds a synthetic tree with known sizes, a hardlink, symlinks (to a file and
# a directory), and a chmod-000 directory to trigger EACCES, then validates:
#   1. scanned subtree bytes == `du -x --block-size=1 -s <tmpdir>`
#   2. hardlinked inode counted exactly once
#   3. symlinks not descended / not double-counted
#   4. chmod-000 dir appears in "blocked", scan still exits 0
#   5. output JSON parses, including paths with non-UTF8 bytes
#   6. configurable output directory is honored
#
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$SCRIPT_DIR/hstscan"
PY=python3

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

[ -x "$BIN" ] || fail "binary not built: $BIN (run make)"

TMP="$(mktemp -d "${TMPDIR:-/tmp}"/hstscan_test.XXXXXX)"
OUT="$(mktemp "${TMPDIR:-/tmp}"/hstscan_out.XXXXXX.json)"
cleanup() {
    # restore perms so rm can recurse
    chmod -R u+rwx "$TMP" 2>/dev/null || true
    rm -rf "$TMP"
    rm -f "$OUT" "$OUT.tmp"
}
trap cleanup EXIT

TREE="$TMP/tree"
mkdir -p "$TREE/sub"

# Known file sizes (bytes).
head -c 1048576  /dev/zero > "$TREE/a.bin"        # 1 MiB
head -c 524288   /dev/zero > "$TREE/b.bin"        # 512 KiB
head -c 2097152  /dev/zero > "$TREE/sub/c.bin"    # 2 MiB

# Hardlinked big file: two links to the SAME inode -> count once.
head -c 4194304  /dev/zero > "$TREE/sub/big.bin"  # 4 MiB
ln "$TREE/sub/big.bin" "$TREE/hardlink_to_big.bin"

# Symlink to a file and to a directory: MUST NOT be followed/counted.
ln -s "$TREE/a.bin" "$TREE/link_to_a"
ln -s "$TREE/sub"   "$TREE/link_to_sub"

# chmod-000 dir to trigger EACCES (put a file in it first).
mkdir -p "$TREE/noaccess"
head -c 1048576 /dev/zero > "$TREE/noaccess/hidden.bin"
chmod 000 "$TREE/noaccess"

# Non-UTF8 filename regression: raw bytes such as 0xff must not make scanner
# output invalid UTF-8 JSON.
NONUTF8_DIR="$TREE/nonutf8"
mkdir -p "$NONUTF8_DIR"
$PY - "$NONUTF8_DIR" <<'PYEOF'
import os, sys
root = sys.argv[1].encode()
with open(os.path.join(root, b"bad-\xff-name.bin"), "wb") as fh:
    fh.write(b"\0" * 4096)
PYEOF

UTF8_DIR="$TMP/utf8"
mkdir -p "$UTF8_DIR"
$PY - "$UTF8_DIR" <<'PYEOF'
import os, sys
root = os.fsencode(sys.argv[1])
for name in ("보고서.txt", "été.txt", "emoji😀.txt", 'quote"\r\n.txt'):
    with open(os.path.join(root, name.encode()), "wb") as fh:
        fh.write(b"x")
for name in (b"bad-\xff.txt", b"short-\xf0", b"surrogate-\xed\xa0\x80"):
    with open(os.path.join(root, name), "wb") as fh:
        fh.write(b"x")
PYEOF
UTF8_DEV="$($PY -c 'import os, sys; dev = os.stat(sys.argv[1]).st_dev; print(f"{os.major(dev)}:{os.minor(dev)}")' "$UTF8_DIR")"
"$BIN" --threads 2 --top 20 --out "$TMP/utf8.json" --target "$UTF8_DIR" "$UTF8_DEV" data \
    || fail "UTF-8 fixture scan failed"
$PY - "$TMP/utf8.json" "$UTF8_DIR" <<'PYEOF' || fail "JSON paths did not preserve UTF-8 or the raw bytes of non-UTF-8 names"
import base64, json, os, sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
def rows(value):
    if isinstance(value, dict):
        nested = rows(list(value.values()))
        if "path" in value:
            nested[value["path"]] = value.get("path_b64")
        return nested
    if isinstance(value, list):
        merged = {}
        for child in value:
            merged.update(rows(child))
        return merged
    return {}

actual = rows(payload)
root = os.fsencode(sys.argv[2])
for name in ("보고서.txt", "été.txt", "emoji😀.txt", 'quote"\r\n.txt'):
    path = str(Path(sys.argv[2]) / name)
    assert path in actual and actual[path] is None, (path, actual)
for display, raw in (("bad-�.txt", b"bad-\xff.txt"), ("short-�", b"short-\xf0"),
                     ("surrogate-���", b"surrogate-\xed\xa0\x80")):
    path = str(Path(sys.argv[2]) / display)
    assert path in actual, (path, actual)
    assert actual[path] is not None, ("missing path_b64", path)
    assert base64.b64decode(actual[path]) == root + b"/" + raw, (path, actual[path])
PYEOF

# Reference: du disk usage of the whole tree (one filesystem, block size 1).
# du dedups hardlinks and does not follow symlinks, matching hstscan.
# Run du with the dir readable but noaccess unreadable -> du also reports the
# noaccess dir's own block usage but can't descend; hstscan behaves the same.
DU_BYTES="$(du -x --block-size=1 -s "$TREE" 2>/dev/null | cut -f1)"
[ -n "$DU_BYTES" ] || fail "du produced no output"

# Guarded target parser and safety regression checks.
read -r TREE_MAJOR TREE_MINOR <<EOF
$($PY - "$TREE" <<'PYEOF'
import os, sys
st = os.lstat(sys.argv[1])
print(os.major(st.st_dev), os.minor(st.st_dev))
PYEOF
)
EOF
TREE_DEV="$TREE_MAJOR:$TREE_MINOR"

GUARDED_OUT="$(mktemp "${TMPDIR:-/tmp}"/hstscan_guarded.XXXXXX.json)"
"$BIN" --threads 2 --prune-home 0 --prune-data 0 --top 10 --out "$GUARDED_OUT" --target "$TREE" "$TREE_DEV" data
GRC=$?
[ "$GRC" -eq 0 ] || fail "guarded --target scan exited non-zero ($GRC)"
$PY - "$GUARDED_OUT" "$TREE" <<'PYEOF' || fail "guarded --target did not scan the exact requested root"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert [m["path"] for m in payload["mounts"]] == [sys.argv[2]]
PYEOF
rm -f "$GUARDED_OUT" "$GUARDED_OUT.tmp"
pass "guarded --target PATH MAJOR:MINOR CLASS scans exact absolute target"

# A mount's immediate child directories are navigation landmarks (for example
# /data/alice and /data/project-a), not expendable detail. They, and one more
# descendant directory level, must remain in the published tree even when their
# contents are smaller than the configured pruning threshold. Deeper small
# descendants should still be folded below that extra preserved level so
# snapshots remain bounded.
PRUNE_ROOT="$TMP/prune-root"
mkdir -p "$PRUNE_ROOT/alice/tiny-work" "$PRUNE_ROOT/bob"
head -c 4096 /dev/zero > "$PRUNE_ROOT/alice/tiny-work/file.bin"
PRUNE_OUT="$TMP/hstscan_prune_root.json"
"$BIN" --threads 2 --prune-home 1 --prune-data 1 --top 10 \
    --out "$PRUNE_OUT" --target "$PRUNE_ROOT" "$TREE_DEV" data
PRC=$?
[ "$PRC" -eq 0 ] || fail "top-level navigation pruning scan exited non-zero ($PRC)"
$PY - "$PRUNE_OUT" <<'PYEOF' || fail "small immediate child directories were pruned from mount navigation"
import json, sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
tree = payload["mounts"][0]["tree"]
children = {child["name"]: child for child in tree.get("children", [])}
assert set(children) == {"alice", "bob"}, children
alice = children["alice"]
workspaces = {child["name"]: child for child in alice.get("children", [])}
assert set(workspaces) == {"tiny-work"}, workspaces
assert workspaces["tiny-work"].get("children", []) == [], workspaces["tiny-work"]
assert alice["bytes"] == workspaces["tiny-work"]["bytes"] + alice["other_bytes"], alice
PYEOF
pass "mount-level child directories and one extra descendant level survive size pruning while deeper detail remains bounded"

# Root-preserved navigation needs one additional retained directory level so
# small nested workspaces remain drillable, but threshold pruning must still
# resume below that point.
DEPTH_ROOT="$TMP/depth-root"
mkdir -p "$DEPTH_ROOT/user/project/tiny"
head -c 4096 /dev/zero > "$DEPTH_ROOT/user/project/tiny/file.bin"
DEPTH_OUT="$TMP/hstscan_depth_root.json"
"$BIN" --threads 2 --prune-home 1 --prune-data 1 --top 10 \
    --out "$DEPTH_OUT" --target "$DEPTH_ROOT" "$TREE_DEV" data
DRC=$?
[ "$DRC" -eq 0 ] || fail "second-level navigation pruning scan exited non-zero ($DRC)"
$PY - "$DEPTH_OUT" <<'PYEOF' || fail "second-level nested directories were pruned from mount navigation"
import json, sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
tree = payload["mounts"][0]["tree"]
children = {child["name"]: child for child in tree.get("children", [])}
assert set(children) == {"user"}, children
user = children["user"]
projects = {child["name"]: child for child in user.get("children", [])}
assert set(projects) == {"project"}, projects
project = projects["project"]
assert project.get("children", []) == [], project
assert user["bytes"] == project["bytes"] + user["other_bytes"], user
PYEOF
pass "mount-level navigation preserves one extra directory level before threshold pruning resumes"

# Root navigation preservation must stay within the collector's per-node child
# contract.  Create one more tiny workspace than the limit and verify the
# scanner emits a deterministic, bounded root while folding the omitted inode
# bytes into other_bytes.
BOUNDED_ROOT="$TMP/bounded-root"
mkdir -p "$BOUNDED_ROOT"
$PY - "$BOUNDED_ROOT" <<'PYEOF'
import os, sys

root = sys.argv[1]
for index in range(16_385):
    os.mkdir(os.path.join(root, f"workspace-{index:05d}"))
PYEOF
BOUNDED_OUT="$TMP/hstscan_bounded_root.json"
"$BIN" --threads 4 --prune-home 1 --prune-data 1 --top 10 \
    --out "$BOUNDED_OUT" --target "$BOUNDED_ROOT" "$TREE_DEV" data
BRC=$?
[ "$BRC" -eq 0 ] || fail "bounded top-level navigation scan exited non-zero ($BRC)"
$PY - "$BOUNDED_OUT" "$BOUNDED_ROOT" <<'PYEOF' || fail "mount navigation exceeded or nondeterministically applied the child bound"
import json, os, sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
tree = payload["mounts"][0]["tree"]
children = tree.get("children", [])
names = [child["name"] for child in children]
assert len(children) == 16_384, len(children)
assert names == [f"workspace-{index:05d}" for index in range(16_384)], (names[:3], names[-3:])
root = sys.argv[2]
expected_other = (
    os.lstat(root).st_blocks * 512
    + os.lstat(os.path.join(root, "workspace-16384")).st_blocks * 512
)
assert tree["other_bytes"] == expected_other, (tree["other_bytes"], expected_other)
assert tree["bytes"] == sum(child["bytes"] for child in children) + tree["other_bytes"]
PYEOF
pass "mount-level navigation is deterministic and capped at the collector child limit"

# The collector applies the same child limit at every depth.  A retained
# workspace with very high fan-out must therefore be bounded too, including
# when the pruning threshold is zero.
NESTED_ROOT="$TMP/nested-bounded-root"
mkdir -p "$NESTED_ROOT/workspace"
$PY - "$NESTED_ROOT/workspace" <<'PYEOF'
import os, sys

root = sys.argv[1]
for index in range(16_385):
    os.mkdir(os.path.join(root, f"run-{index:05d}"))
PYEOF
NESTED_OUT="$TMP/hstscan_nested_bounded_root.json"
"$BIN" --threads 4 --prune-home 0 --prune-data 0 --top 10 \
    --out "$NESTED_OUT" --target "$NESTED_ROOT" "$TREE_DEV" data
NRC=$?
[ "$NRC" -eq 0 ] || fail "nested bounded navigation scan exited non-zero ($NRC)"
$PY - "$NESTED_OUT" "$NESTED_ROOT/workspace" <<'PYEOF' || fail "nested tree node exceeded or nondeterministically applied the child bound"
import json, os, sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
workspace = payload["mounts"][0]["tree"]["children"][0]
children = workspace.get("children", [])
names = [child["name"] for child in children]
assert len(children) == 16_384, len(children)
assert names == [f"run-{index:05d}" for index in range(16_384)], (names[:3], names[-3:])
root = sys.argv[2]
expected_other = (
    os.lstat(root).st_blocks * 512
    + os.lstat(os.path.join(root, "run-16384")).st_blocks * 512
)
assert workspace["other_bytes"] == expected_other, (workspace["other_bytes"], expected_other)
assert workspace["bytes"] == sum(child["bytes"] for child in children) + workspace["other_bytes"]
PYEOF
pass "nested tree nodes are deterministic and capped at the collector child limit"

# Cross-target hardlink regression: when two guarded targets on the same device
# contain directory entries for the same regular-file inode, the inode must be
# charged once globally, to the first target in CLI order.  Keep the fixture
# limited to target roots plus one hardlinked file so ordinary directory/root
# metadata can be subtracted explicitly from the assertions.
SAMEDEV_HOME="$TMP/home"
SAMEDEV_DATA="$TMP/data"
mkdir -p "$SAMEDEV_HOME" "$SAMEDEV_DATA"
head -c 131072 /dev/zero > "$SAMEDEV_HOME/shared.bin"
ln "$SAMEDEV_HOME/shared.bin" "$SAMEDEV_DATA/shared.bin"
read -r SAMEDEV_MAJOR SAMEDEV_MINOR HOME_ROOT_BYTES DATA_ROOT_BYTES SHARED_BYTES SHARED_UID <<EOF
$($PY - "$SAMEDEV_HOME" "$SAMEDEV_DATA" "$SAMEDEV_HOME/shared.bin" <<'PYEOF'
import os, sys
home, data, shared = sys.argv[1:4]
hst = os.lstat(home)
dst = os.lstat(data)
fst = os.lstat(shared)
if hst.st_dev != dst.st_dev or hst.st_dev != fst.st_dev:
    raise SystemExit("same-device hardlink fixture crossed devices")
print(
    os.major(hst.st_dev),
    os.minor(hst.st_dev),
    hst.st_blocks * 512,
    dst.st_blocks * 512,
    fst.st_blocks * 512,
    fst.st_uid,
)
PYEOF
)
EOF
SAMEDEV_DEV="$SAMEDEV_MAJOR:$SAMEDEV_MINOR"
SAMEDEV_OUT="$TMP/hstscan_samedev_hardlink.json"
"$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 --out "$SAMEDEV_OUT" \
    --target "$SAMEDEV_HOME" "$SAMEDEV_DEV" home \
    --target "$SAMEDEV_DATA" "$SAMEDEV_DEV" data
SRC=$?
[ "$SRC" -eq 0 ] || fail "same-device hardlink guarded scan exited non-zero ($SRC)"
if ! $PY - "$SAMEDEV_OUT" "$SAMEDEV_HOME" "$SAMEDEV_DATA" \
    "$HOME_ROOT_BYTES" "$DATA_ROOT_BYTES" "$SHARED_BYTES" "$SHARED_UID" <<'PYEOF'
import json, sys

out, home, data = sys.argv[1:4]
home_root, data_root, shared_bytes, shared_uid = map(int, sys.argv[4:8])
with open(out, encoding="utf-8") as fh:
    payload = json.load(fh)

mounts = payload.get("mounts", [])
paths = [m.get("path") for m in mounts]
if paths != [home, data]:
    raise SystemExit(f"mount order/path mismatch: {paths!r}")

by_path = {m["path"]: m for m in mounts}
for path, root_bytes in ((home, home_root), (data, data_root)):
    tree = by_path[path].get("tree", {})
    if tree.get("kind") != "directory" or tree.get("children", []) != []:
        raise SystemExit(f"unexpected single-root mount tree for {path}: {tree!r}")
    if tree.get("bytes") != by_path[path]["scanned_bytes"]:
        raise SystemExit(
            f"tree/scanned byte mismatch for {path}: "
            f"tree={tree.get('bytes')} scanned={by_path[path]['scanned_bytes']}"
        )
    if tree.get("bytes", 0) < root_bytes:
        raise SystemExit(f"tree bytes for {path} are less than root metadata bytes")

home_file_bytes = by_path[home]["tree"]["bytes"] - home_root
data_file_bytes = by_path[data]["tree"]["bytes"] - data_root
if home_file_bytes != shared_bytes:
    raise SystemExit(
        f"first target file contribution {home_file_bytes}, expected {shared_bytes}"
    )
if data_file_bytes != 0:
    raise SystemExit(f"second target file contribution {data_file_bytes}, expected 0")
if by_path[home]["scanned_files"] != 1 or by_path[data]["scanned_files"] != 0:
    raise SystemExit(
        f"scanned_files mismatch: home={by_path[home]['scanned_files']} "
        f"data={by_path[data]['scanned_files']}"
    )

users = [u for u in payload.get("users", []) if u.get("uid") == shared_uid]
if len(users) != 1:
    raise SystemExit(f"expected exactly one user row for uid {shared_uid}, got {len(users)}")
user = users[0]
if user.get("files") != 1:
    raise SystemExit(f"uid {shared_uid} files={user.get('files')}, expected 1")
by_mount = user.get("by_mount", {})
user_home_file_bytes = by_mount.get(home, 0) - home_root
user_data_file_bytes = by_mount.get(data, 0) - data_root
if user_home_file_bytes != shared_bytes or user_data_file_bytes != 0:
    raise SystemExit(
        f"user by_mount file bytes mismatch: home={user_home_file_bytes} "
        f"data={user_data_file_bytes} expected home={shared_bytes} data=0"
    )

top = payload.get("top_files", [])
if len(top) != 1 or top[0].get("path") != home + "/shared.bin":
    raise SystemExit(f"top_files should contain only first-target hardlink path, got {top!r}")
if top[0].get("bytes") != shared_bytes or top[0].get("uid") != shared_uid:
    raise SystemExit(f"top_files metadata mismatch: {top[0]!r}")
PYEOF
then
    fail "same-device hardlink was not counted once and attributed to first target"
fi
rm -f "$SAMEDEV_OUT" "$SAMEDEV_OUT.tmp"
pass "same-device hardlink across guarded /home then /data targets is counted once and attributed to /home"

MISMATCH_OUT="$(mktemp "${TMPDIR:-/tmp}"/hstscan_mismatch.XXXXXX.json)"
if [ "$TREE_DEV" = "0:0" ]; then WRONG_DEV="1:0"; else WRONG_DEV="0:0"; fi
"$BIN" --threads 2 --prune-home 0 --prune-data 0 --top 10 --out "$MISMATCH_OUT" --target "$TREE" "$WRONG_DEV" data
MRC=$?
[ "$MRC" -eq 0 ] || fail "mismatched guarded target exited non-zero ($MRC)"
$PY - "$MISMATCH_OUT" <<'PYEOF' || fail "mismatched guarded target was not skipped safely"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["mounts"] == []
PYEOF
rm -f "$MISMATCH_OUT" "$MISMATCH_OUT.tmp"
pass "guarded target device mismatch skips without traversal"

for args in \
    "--target" \
    "--target relative 1:2 data" \
    "--target $TREE" \
    "--target $TREE 1 data" \
    "--target $TREE 1: data" \
    "--target $TREE :2 data" \
    "--target $TREE +1:2 data" \
    "--target $TREE -1:2 data" \
    "--target $TREE 1:-2 data" \
    "--target $TREE 1:2x data" \
    "--target $TREE 999999999999999999999999999999:0 data" \
    "--target $TREE $TREE_DEV" \
    "--target $TREE $TREE_DEV root" \
    "--target $TREE $TREE_DEV HOME" \
    "--target $TREE $TREE_DEV $TREE" \
    "--target $TREE $TREE_DEV data $TREE"; do
    # shellcheck disable=SC2086
    if "$BIN" --out "$TMP/invalid.json" $args >/dev/null 2>"$TMP/invalid.err"; then
        IRC=0
    else
        IRC=$?
    fi
    [ "$IRC" -eq 2 ] || fail "invalid guarded args [$args] exited $IRC, expected 2"
done
pass "guarded --target parser rejects malformed, non-absolute, classless and mixed positional targets"

TOO_MANY_GUARDED=()
for n in $(seq 1 65); do
    TOO_MANY_GUARDED+=(--target "$TREE" "$TREE_DEV" data)
done
if "$BIN" --out "$TMP/too-many-guarded.json" "${TOO_MANY_GUARDED[@]}" >/dev/null 2>"$TMP/too-many-guarded.err"; then
    TMRC=0
else
    TMRC=$?
fi
[ "$TMRC" -eq 2 ] || fail "65 guarded targets exited $TMRC, expected usage exit 2"
pass "65 guarded targets are rejected instead of silently dropped"

TOO_MANY_POSITIONAL=()
for n in $(seq 1 65); do
    TOO_MANY_POSITIONAL+=("$TREE")
done
if "$BIN" --out "$TMP/too-many-positional.json" "${TOO_MANY_POSITIONAL[@]}" >/dev/null 2>"$TMP/too-many-positional.err"; then
    PMRC=0
else
    PMRC=$?
fi
[ "$PMRC" -eq 2 ] || fail "65 positional targets exited $PMRC, expected usage exit 2"
pass "65 positional targets are rejected instead of silently dropped"

$PY - "$SCRIPT_DIR/hstscan.c" <<'PYEOF' || fail "descriptor-relative walk contract violated"
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
def body(name, next_marker):
    start = s.find(name)
    end = s.find(next_marker, start)
    if start == -1 or end == -1:
        raise SystemExit(f"{name} bounds not found")
    return s[start:end]
verified = body("static int open_verified(", "static int reopen_from_root(")
if "openat(parent_fd, n->name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)" not in verified:
    raise SystemExit("directories must open relative to the parent fd with O_NOFOLLOW | O_DIRECTORY")
if not re.search(r"fstat\(fd, &st\)[\s\S]*st\.st_dev != n->dev \|\| st\.st_ino != n->ino", verified):
    raise SystemExit("open_verified must fstat the new fd and compare (dev, ino) with the parent's fstatat")
walk = body("static void process_dir(", "static void *worker_main(")
if re.search(r"(?<![a-z_])open\(", walk) or "openat(" in walk:
    raise SystemExit("process_dir must open directories only through open_verified/reopen_from_root")
if walk.find("st.st_dev != c->root_dev") > walk.find("queue_child(") or "st.st_dev != c->root_dev" not in walk:
    raise SystemExit("the one-file-system device guard must run before a child is queued")
if "SYS_getdents64" not in walk or walk.find("reopen_from_root") > walk.find("SYS_getdents64"):
    raise SystemExit("a parked directory must be reopened and verified before getdents64")
PYEOF
pass "directories open relative to a verified parent fd with identity and device checks before enumeration"

$PY - "$SCRIPT_DIR/hstscan.c" <<'PYEOF' || fail "root preflight contract violated: open+fstat must happen before root node creation"
import sys
s = open(sys.argv[1], encoding="utf-8").read()
start = s.find("static struct node *scan_target(")
end = s.find("/* ----------------------------------------------------------------------- *\n *  Mount discovery", start)
if start == -1 or end == -1:
    raise SystemExit("scan_target bounds not found")
body = s[start:end]
open_i = body.find("open(")
fstat_i = body.find("fstat(rootfd")
node_i = body.find("node_new")
if not (open_i != -1 and fstat_i != -1 and node_i != -1 and open_i < fstat_i < node_i):
    raise SystemExit(f"open={open_i} fstat={fstat_i} node_new={node_i}")
if "opened_root_dev != rst.st_dev" not in body or "blocked_add(target" not in body:
    raise SystemExit("root open/lstat device race must be visible and not produce root-only success")
if "expected_dev && opened_root_dev != *expected_dev" not in body:
    raise SystemExit("guarded expected-device mismatch must be checked against opened root fd")
PYEOF
pass "root preflight source contract open+fstat before node creation is present"

$PY - "$SCRIPT_DIR/hstscan.c" <<'PYEOF' || fail "usage contract missing guarded --target documentation"
import sys
s = open(sys.argv[1], encoding="utf-8").read()
if "--target PATH MAJOR:MINOR CLASS" not in s or "Do not mix" not in s:
    raise SystemExit("usage must document repeated guarded --target and exclusivity")
PYEOF
pass "usage documents repeated guarded --target and positional exclusivity"

# Run the scanner on the tree, no pruning so the whole subtree is retained.
"$BIN" --threads 4 --prune-home 0 --prune-data 0 --top 50 --out "$OUT" "$TREE"
RC=$?
[ "$RC" -eq 0 ] || fail "hstscan exited non-zero ($RC)"
pass "scan completed with exit 0 (EACCES handled gracefully)"

# JSON parses.
$PY -c 'import json,sys; json.load(open(sys.argv[1]))' "$OUT" \
    || fail "output JSON did not parse"
pass "output JSON parses"

$PY - "$OUT" <<'PYEOF' || fail "output JSON did not parse as explicit UTF-8"
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    json.load(fh)
PYEOF
pass "output JSON parses with explicit UTF-8 despite non-UTF8 filename bytes"

$PY - "$OUT" <<'PYEOF' || fail "kind fields missing or invalid"
import json, sys
d = json.load(open(sys.argv[1]))
assert d["mounts"][0]["tree"]["kind"] == "directory"
assert all(row["kind"] == "file" for row in d.get("top_files", []))
assert all(row["kind"] == "file" for row in d.get("stale", []))
PYEOF
pass "kind fields identify directory tree nodes and file rows"

$PY - "$OUT" <<'PYEOF' || fail "tree byte accounting invariant is invalid"
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)

def validate(node):
    children = node.get("children", [])
    for child in children:
        validate(child)
    other = node.get("other_bytes", 0)
    if children and node["bytes"] != sum(child["bytes"] for child in children) + other:
        raise SystemExit(
            f"{node['name']}: bytes={node['bytes']} "
            f"children={sum(child['bytes'] for child in children)} other={other}"
        )
    if not children and other != 0:
        raise SystemExit(f"{node['name']}: leaf other_bytes={other}")

for mount in payload["mounts"]:
    validate(mount["tree"])
PYEOF
pass "tree bytes equal retained child bytes plus other_bytes"

$PY - "$OUT" "$TREE" <<'PYEOF' || fail "user by_mount keys do not match the requested scan root"
import json, sys

with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)
expected = sys.argv[2]
keys = {mount for user in payload.get("users", []) for mount in user.get("by_mount", {})}
if keys != {expected}:
    raise SystemExit(f"expected by_mount {{{expected!r}}}, got {sorted(keys)!r}")
PYEOF
pass "user by_mount keys reference the requested scan root"

# Pull figures out with python for robust assertions.
read -r SCAN_BYTES BLOCKED_HAS_NOACCESS NODE_NAMES <<EOF
$($PY - "$OUT" "$TREE" <<'PYEOF'
import json,sys
out, tree = sys.argv[1], sys.argv[2]
d = json.load(open(out))
m = d["mounts"][0]
t = m["tree"]
if t.get("kind") != "directory":
    raise SystemExit(f"tree root kind is {t.get('kind')!r}, expected directory")
for child in t.get("children", []):
    if child.get("kind") != "directory":
        raise SystemExit(f"tree child {child.get('name')!r} kind is {child.get('kind')!r}, expected directory")
for row in d.get("top_files", []):
    if row.get("kind") != "file":
        raise SystemExit(f"top_files row kind is {row.get('kind')!r}, expected file")
for row in d.get("stale", []):
    if row.get("kind") != "file":
        raise SystemExit(f"stale row kind is {row.get('kind')!r}, expected file")
# top-level tree node corresponds to the scanned target (the tree dir)
scan_bytes = t["bytes"]
# names of immediate children (to confirm symlinks are not present as dirs)
names = ",".join(sorted(c["name"] for c in t.get("children", [])))
# blocked contains the noaccess dir?
blocked = d.get("blocked", [])
has_noaccess = any("noaccess" in b["path"] for b in blocked)
print(scan_bytes, "1" if has_noaccess else "0", names if names else "-")
PYEOF
)
EOF

# 1. du vs scanner equality.
echo "  du bytes      = $DU_BYTES"
echo "  scanner bytes = $SCAN_BYTES"
if [ "$DU_BYTES" = "$SCAN_BYTES" ]; then
    pass "scanner subtree bytes == du -x --block-size=1 -s"
else
    fail "byte mismatch: du=$DU_BYTES scanner=$SCAN_BYTES"
fi

# 6. Portable output directory: --out-dir writes <hostname>.json under the
# requested directory rather than relying on source-tree-specific paths.
OUTDIR="$TMP/outdir"
mkdir -p "$OUTDIR"
"$BIN" --threads 2 --prune-home 0 --prune-data 0 --top 5 --out-dir "$OUTDIR" "$TREE" \
    >/dev/null 2>"$TMP/outdir.err"
OUTDIR_RC=$?
[ "$OUTDIR_RC" -eq 0 ] || fail "--out-dir scan exited non-zero ($OUTDIR_RC): $(cat "$TMP/outdir.err")"
HOSTNAME="$($PY - <<'PYEOF'
import socket
print(socket.gethostname())
PYEOF
)"
OUTDIR_FILE="$OUTDIR/$HOSTNAME.json"
[ -f "$OUTDIR_FILE" ] || fail "--out-dir did not create $OUTDIR_FILE"
$PY - "$OUTDIR_FILE" <<'PYEOF' || fail "--out-dir output JSON did not parse"
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    json.load(fh)
PYEOF
pass "--out-dir writes parseable data/<hostname>.json in requested directory"

CWDRUN="$TMP/cwd-default"
mkdir -p "$CWDRUN/data"
( cd "$CWDRUN" && "$BIN" --threads 2 --prune-home 0 --prune-data 0 --top 5 "$TREE" \
    >/dev/null 2>"$TMP/cwd-default.err" )
CWD_RC=$?
[ "$CWD_RC" -eq 0 ] || fail "default CWD output scan exited non-zero ($CWD_RC): $(cat "$TMP/cwd-default.err")"
CWD_OUT="$CWDRUN/data/$HOSTNAME.json"
[ -f "$CWD_OUT" ] || fail "default output did not create $CWD_OUT"
$PY - "$CWD_OUT" <<'PYEOF' || fail "default CWD output JSON did not parse"
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    json.load(fh)
PYEOF
pass "default output writes data/<hostname>.json relative to current working directory"

# 4. blocked contains noaccess.
if [ "$BLOCKED_HAS_NOACCESS" = "1" ]; then
    pass "chmod-000 dir present in 'blocked'"
else
    fail "noaccess dir not found in blocked list"
fi

# 3. symlinks not represented as descended dirs (link_to_sub must not be a child
#    node, and link_to_a must not appear). Children should only be real dirs:
#    "noaccess" and "sub".
echo "  tree children = $NODE_NAMES"
case ",$NODE_NAMES," in
    *",link_to_sub,"*) fail "symlinked dir was descended (appears as child node)";;
esac
pass "symlinked directory was not descended"

# 2. Hardlink dedup cross-check: compute expected bytes independently and ensure
#    big.bin (4MiB) is counted once. We verify by comparing to du, which also
#    dedups; equality above already proves single-count, but assert explicitly:
#    a tree WITHOUT dedup would exceed du by big.bin's block size.
BIG_BLOCKS="$(stat -c '%b' "$TREE/sub/big.bin")"
BIG_BYTES=$(( BIG_BLOCKS * 512 ))
DOUBLE=$(( DU_BYTES + BIG_BYTES ))
if [ "$SCAN_BYTES" = "$DOUBLE" ]; then
    fail "hardlink appears double-counted (scanner=$SCAN_BYTES, no-dedup=$DOUBLE)"
fi
pass "hardlinked inode counted once (no double-count of big.bin)"

# 5. fd-exhaustion regression guard: a WIDE+DEEP tree (thousands of dirs)
#    scanned under a LOW hard fd limit must still exactly match du with zero
#    "Too many open files" errors. Concurrently-open dir fds must be bounded by
#    the thread count, not the queue depth.
WIDE="$TMP/wide"
$PY - "$WIDE" <<'PYEOF'
import os, sys
root = sys.argv[1]
# 30 x 10 x 4 = 1200 leaf dirs across 3 levels, each with a small file and a
# block-consuming (long-target) symlink to also exercise symlink accounting.
for a in range(30):
    for b in range(10):
        for c in range(4):
            d = os.path.join(root, f"a{a}", f"b{b}", f"c{c}")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "f.bin"), "wb") as fh:
                fh.write(b"\0" * 4096)
            try:
                os.symlink("t" * 200, os.path.join(d, "lnk"))
            except FileExistsError:
                pass
PYEOF
WIDE_DU="$(du -sx --block-size=1 "$WIDE" | cut -f1)"
WIDE_OUT="$(mktemp "${TMPDIR:-/tmp}"/hstscan_wide.XXXXXX.json)"
# Hard-cap fds to 128 in a subshell; 16 threads. If fds grew with queue depth
# this would hit EMFILE and undercount.
( ulimit -n 128 2>/dev/null; "$BIN" --threads 16 --prune-home 0 --prune-data 0 \
    --out "$WIDE_OUT" "$WIDE" 2>"$TMP/wide.err" )
WRC=$?
[ "$WRC" -eq 0 ] || fail "wide-tree scan exited non-zero ($WRC)"
if grep -q "Too many open files" "$TMP/wide.err" 2>/dev/null; then
    fail "fd exhaustion: 'Too many open files' during wide-tree scan"
fi
read -r WIDE_SCAN WIDE_ERRS <<EOF
$($PY - "$WIDE_OUT" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
m = d["mounts"][0]
print(m["scanned_bytes"], m["errors"])
PYEOF
)
EOF
rm -f "$WIDE_OUT" "$WIDE_OUT.tmp"
echo "  wide du   = $WIDE_DU"
echo "  wide scan = $WIDE_SCAN (errors=$WIDE_ERRS, ulimit -n 128, 16 threads)"
[ "$WIDE_ERRS" = "0" ] || fail "wide-tree scan reported $WIDE_ERRS errors (expected 0)"
if [ "$WIDE_DU" = "$WIDE_SCAN" ]; then
    pass "wide+deep tree under low fd limit: exact du match, no EMFILE"
else
    fail "wide-tree byte mismatch: du=$WIDE_DU scanner=$WIDE_SCAN"
fi

reopened_count() {
    sed -n 's/^hstscan: \([0-9][0-9]*\) parked dir(s) reopened from the pinned root.*/\1/p' "$1"
}

# The same tree under ulimit -n 64. The queued-descriptor budget is then 16,
# below the tree's fan-out, so directories are parked and later reopened one
# component at a time from the pinned root. Totals must still match du.
( ulimit -n 64; "$BIN" --threads 16 --prune-home 0 --prune-data 0 \
    --out "$TMP/wide64.json" "$WIDE" 2>"$TMP/wide64.err" )
W64RC=$?
[ "$W64RC" -eq 0 ] || fail "wide-tree scan under ulimit -n 64 exited non-zero ($W64RC): $(cat "$TMP/wide64.err")"
read -r WIDE64_SCAN WIDE64_ERRS <<EOF
$($PY - "$TMP/wide64.json" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1]))["mounts"][0]
print(m["scanned_bytes"], m["errors"])
PYEOF
)
EOF
WIDE64_REOPENED="$(reopened_count "$TMP/wide64.err")"
echo "  wide scan = $WIDE64_SCAN (errors=$WIDE64_ERRS, reopened=$WIDE64_REOPENED, ulimit -n 64, 16 threads)"
[ "$WIDE64_ERRS" = "0" ] || fail "wide-tree scan under ulimit -n 64 reported $WIDE64_ERRS errors"
[ "$WIDE_DU" = "$WIDE64_SCAN" ] || fail "wide-tree byte mismatch under ulimit -n 64: du=$WIDE_DU scanner=$WIDE64_SCAN"
[ "${WIDE64_REOPENED:-0}" -gt 0 ] || fail "ulimit -n 64 run never parked a directory, so it did not exercise reopen"
pass "wide+deep tree under ulimit -n 64: parked directories reopened from the pinned root, exact du match"

# A chain far deeper than any recursion could survive on a 1 MiB stack, with
# one-byte names. Totals must match du with no errors. The prune threshold
# folds the chain below the two preserved levels so the output stays shallow
# enough for json.load.
DEEP_CHAIN="$TMP/deep-chain"
$PY - "$DEEP_CHAIN" <<'PYEOF'
import os, sys
os.mkdir(sys.argv[1])
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
for _ in range(50_000):
    os.mkdir("d", dir_fd=fd)
    child = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
    os.close(fd)
    fd = child
leaf = os.open("leaf.bin", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd)
os.write(leaf, b"\1" * 65536)
os.close(leaf)
os.close(fd)
PYEOF
DEEP_CHAIN_DU="$(du -x --block-size=1 -s "$DEEP_CHAIN" | cut -f1)"
( ulimit -s 1024; "$BIN" --threads 2 --prune-data 1000000 --top 10 --out "$TMP/deep-chain.json" \
    --target "$DEEP_CHAIN" "$TREE_DEV" data 2>"$TMP/deep-chain.err" )
DCRC=$?
[ "$DCRC" -eq 0 ] || fail "50000-level chain scan exited $DCRC on a 1 MiB stack: $(cat "$TMP/deep-chain.err")"
$PY - "$TMP/deep-chain.json" "$DEEP_CHAIN_DU" <<'PYEOF' || fail "50000-level chain totals do not match du"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
mount = payload["mounts"][0]
assert mount["errors"] == 0, mount["errors"]
assert mount["scanned_bytes"] == int(sys.argv[2]), (mount["scanned_bytes"], sys.argv[2])
assert mount["scanned_dirs"] == 50_001, mount["scanned_dirs"]
PYEOF
pass "50000-level chain on a 1 MiB stack: no recursion by depth, exact du match"

# Catalogued scanner bugs (docs/redesign/bug-catalog.md). M0 landed each one
# as a strict expected failure. M4 fixed A1, A5, A6 and A7, so their cases are
# required now. A newly catalogued scanner bug goes through xfail_case until
# its fix lands, and the expected count below changes with it.
XFAIL_COUNT=0
xfail_case() {
    local name="$1" bug="$2" log="$TMP/xfail-$1.log"
    shift 2
    if ( "$@" ) >"$log" 2>&1; then
        cat "$log" >&2
        fail "XPASS(strict) $name: $bug no longer reproduces; remove its xfail_case wrapper"
    fi
    if ! grep -q "^BUG $bug: " "$log"; then
        cat "$log" >&2
        fail "$name failed without the BUG $bug line (setup error or a different failure)"
    fi
    XFAIL_COUNT=$((XFAIL_COUNT + 1))
    echo "XFAIL: $name: $(grep -m1 "^BUG $bug: " "$log")"
}

required_case() {
    local name="$1" log="$TMP/case-$1.log"
    shift
    if ! ( "$@" ) >"$log" 2>&1; then
        cat "$log" >&2
        fail "$name"
    fi
    pass "$name"
}

dev_of() {
    $PY -c 'import os, sys; d = os.lstat(sys.argv[1]).st_dev; print("%d:%d" % (os.major(d), os.minor(d)))' "$1"
}

du_bytes() {
    du -x --block-size=1 -s "$1" | cut -f1
}

# A1 needs a rename to land at a chosen moment of the walk. An LD_PRELOAD hook
# makes that deterministic: when an open/openat resolves to $A1_TRIGGER
# (default $A1_VICTIM) for the first time after $A1_SKIP (default 0) earlier
# matches, it renames $A1_VICTIM to $A1_MOVED_TO (and with A1_REPLACE=1
# recreates an empty directory in its place) before the real call. The scanner
# opens each child while it enumerates the parent, so with --threads 1 a hook
# on a child fires between the parent's fstatat and the child's openat.
A1_HOOK="$TMP/a1_rename_hook.so"
cat > "$TMP/a1_rename_hook.c" <<'CEOF'
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef int (*open_fn)(const char *, int, ...);
typedef int (*openat_fn)(int, const char *, int, ...);
static int fired, seen;

static void maybe_fire(const char *path) {
    const char *victim = getenv("A1_VICTIM");
    const char *trigger = getenv("A1_TRIGGER");
    const char *skip = getenv("A1_SKIP");
    if (!trigger) trigger = victim;
    if (fired || !victim || strcmp(path, trigger) != 0) return;
    if (skip && seen++ < atoi(skip)) return;
    fired = 1;
    if (rename(victim, getenv("A1_MOVED_TO")) != 0) _exit(97);
    const char *replace = getenv("A1_REPLACE");
    if (replace && strcmp(replace, "1") == 0 && mkdir(victim, 0755) != 0) _exit(98);
}

static mode_t mode_arg(int flags, va_list ap) {
    return (flags & (O_CREAT | O_TMPFILE)) ? (mode_t)va_arg(ap, int) : 0;
}

int open(const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t mode = mode_arg(flags, ap); va_end(ap);
    maybe_fire(path);
    return ((open_fn)dlsym(RTLD_NEXT, "open"))(path, flags, mode);
}

int open64(const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t mode = mode_arg(flags, ap); va_end(ap);
    maybe_fire(path);
    return ((open_fn)dlsym(RTLD_NEXT, "open64"))(path, flags, mode);
}

int openat(int dirfd, const char *name, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t mode = mode_arg(flags, ap); va_end(ap);
    char link[64], dir[PATH_MAX], full[PATH_MAX * 2];
    snprintf(link, sizeof link, "/proc/self/fd/%d", dirfd);
    ssize_t n = name[0] == '/' ? -1 : readlink(link, dir, sizeof dir - 1);
    if (n > 0) {
        dir[n] = '\0';
        snprintf(full, sizeof full, "%s/%s", dir, name);
        maybe_fire(full);
    } else {
        maybe_fire(name);
    }
    return ((openat_fn)dlsym(RTLD_NEXT, "openat"))(dirfd, name, flags, mode);
}
CEOF
cc -shared -fPIC -O2 -Wall -Wextra -o "$A1_HOOK" "$TMP/a1_rename_hook.c" -ldl \
    || fail "could not build the A1 rename hook"

# $1 = rename | replace. The victim moves after the root's fstatat saw it and
# before its openat. The scan must not report a complete root: it records a
# VANISHED (rename) or REPLACED (replace) row for the victim.
a1_during_scan() {
    local mode="$1" root="$TMP/a1-$1"
    mkdir -p "$root/tree/victim/inner" "$root/tree/other"
    head -c 1048576 /dev/zero > "$root/tree/victim/inner/f.bin"
    head -c 65536 /dev/zero > "$root/tree/other/g.bin"
    local dev replace=0 reason=VANISHED
    dev="$(dev_of "$root/tree")"
    if [ "$mode" = replace ]; then replace=1; reason=REPLACED; fi
    env A1_VICTIM="$root/tree/victim" A1_MOVED_TO="$root/tree/moved" \
        A1_REPLACE="$replace" LD_PRELOAD="$A1_HOOK" \
        "$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 \
        --out "$root/out.json" --target "$root/tree" "$dev" data \
        || fail "A1 $mode scan exited non-zero"
    [ -d "$root/tree/moved" ] || fail "A1 hook never fired: victim was not opened"
    $PY - "$root/out.json" "$root/tree/victim" "$reason" <<'PYEOF'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
mount, victim, reason = payload["mounts"][0], sys.argv[2], sys.argv[3]
rows = [(b["path"], b["reason"]) for b in payload["blocked"]]
assert (victim, reason) in rows, ("expected a %s row for %s" % (reason, victim), rows)
assert mount["errors"] >= 1 and mount["vanished_dirs"] >= 1, mount
PYEOF
}
required_case a1_rename_during_scan a1_during_scan rename
required_case a1_replace_during_scan a1_during_scan replace

# The victim moves while the scanner enumerates it, after its openat succeeded.
# The walk continues on the pinned descriptor, so the subtree is counted in
# full and the root is complete.
a1_rename_after_open() {
    local root="$TMP/a1-after-open"
    mkdir -p "$root/tree/victim/inner" "$root/tree/other"
    head -c 1048576 /dev/zero > "$root/tree/victim/inner/f.bin"
    head -c 65536 /dev/zero > "$root/tree/other/g.bin"
    local expected dev
    expected="$(du_bytes "$root/tree")"
    dev="$(dev_of "$root/tree")"
    env A1_VICTIM="$root/tree/victim" A1_TRIGGER="$root/tree/victim/inner" \
        A1_MOVED_TO="$root/tree/moved" LD_PRELOAD="$A1_HOOK" \
        "$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 \
        --out "$root/out.json" --target "$root/tree" "$dev" data \
        || fail "A1 rename-after-open scan exited non-zero"
    [ -d "$root/tree/moved" ] || fail "A1 hook never fired: inner was not opened"
    $PY - "$root/out.json" "$expected" <<'PYEOF'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
mount, expected = payload["mounts"][0], int(sys.argv[2])
assert mount["errors"] == 0, payload["blocked"]
assert mount["scanned_bytes"] == expected, (mount["scanned_bytes"], expected)
PYEOF
}
required_case a1_rename_after_open a1_rename_after_open

# Under ulimit -n 64 the budget is 16 descriptors, so most of P's 40 children
# are parked. P is renamed when the first parked child is reopened from the
# root (the second open of P). Every parked child must become a scoped
# FD_BUDGET_REOPEN_FAILED row, never a silent drop.
a1_ancestor_renamed_while_parked() {
    local root="$TMP/a1-parked" i
    mkdir -p "$root/tree/P"
    for i in $(seq -w 0 39); do
        mkdir "$root/tree/P/c$i"
        head -c 65536 /dev/zero > "$root/tree/P/c$i/f.bin"
    done
    local expected dev
    expected="$(du_bytes "$root/tree")"
    dev="$(dev_of "$root/tree")"
    ( ulimit -n 64
      env A1_VICTIM="$root/tree/P" A1_SKIP=1 A1_MOVED_TO="$root/tree/moved" \
        LD_PRELOAD="$A1_HOOK" \
        "$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 \
        --out "$root/out.json" --target "$root/tree" "$dev" data ) \
        || fail "parked-reopen scan exited non-zero"
    [ -d "$root/tree/moved" ] || fail "hook never fired: P was not reopened from the root"
    $PY - "$root/out.json" "$expected" <<'PYEOF'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
mount, expected = payload["mounts"][0], int(sys.argv[2])
reasons = {b["reason"] for b in payload["blocked"]}
assert mount["errors"] > 0, "parked children vanished without an error"
assert reasons == {"FD_BUDGET_REOPEN_FAILED"}, reasons
assert mount["vanished_dirs"] == mount["errors"], mount
assert mount["scanned_bytes"] < expected, (mount["scanned_bytes"], expected)
print("parked children lost: %d" % mount["errors"])
PYEOF
}
required_case a1_ancestor_renamed_while_parked a1_ancestor_renamed_while_parked

# A path over 4096 bytes, scanned under ulimit -n 64. The leaf directory has 40
# subdirectories, more than the descriptor budget of 16. With one worker
# nothing drains the queue while the leaf is enumerated, so 25 children are
# parked and later reopened through all 25 components from the root.
a5_path_over_4096() {
    local root="$TMP/a5"
    $PY - "$root" <<'PYEOF'
import os, sys
os.mkdir(sys.argv[1])
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
for level in range(24):  # 24 x 201 bytes: the leaf path is over 4096 bytes long
    name = "d%02d" % level + "n" * 197
    os.mkdir(name, dir_fd=fd)
    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
    os.close(fd)
    fd = child
leaf = os.open("deep.bin", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd)
os.write(leaf, b"\1" * 1048576)
os.close(leaf)
for index in range(40):
    name = "fan%02d" % index
    os.mkdir(name, dir_fd=fd)
    sub = os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
    small = os.open("f.bin", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=sub)
    os.write(small, b"\1" * 4096)
    os.close(small)
    os.close(sub)
os.close(fd)
PYEOF
    local expected dev
    expected="$(du_bytes "$root")"
    dev="$(dev_of "$root")"
    ( ulimit -n 64; "$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 \
        --out "$TMP/a5.json" --target "$root" "$dev" data 2>"$TMP/a5.err" ) \
        || fail "A5 scan exited non-zero"
    local reopened
    reopened="$(reopened_count "$TMP/a5.err")"
    [ "${reopened:-0}" -gt 0 ] || fail "A5 fan-out never parked a directory (reopened=${reopened:-none})"
    $PY - "$TMP/a5.json" "$expected" <<'PYEOF'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
mount, expected = payload["mounts"][0], int(sys.argv[2])
assert mount["errors"] == 0, payload["blocked"]
assert mount["scanned_bytes"] == expected, (mount["scanned_bytes"], expected)
# Rows longer than 4096 bytes would make the agent reject the whole output.
for key in ("top_files", "stale", "blocked"):
    for row in payload[key]:
        assert len(row["path"].encode()) <= 4096, (key, len(row["path"]))
PYEOF
}
required_case a5_path_over_4096 a5_path_over_4096

# Same-shape home and data trees with different thresholds and no "/" target.
# --prune-home 0 keeps every home directory; --prune-data 1 folds the data
# tree's small third level. Each target names its prune class.
a6_prune_class() {
    local root="$TMP/a6" class
    for class in home data; do
        mkdir -p "$root/$class/user/project/deep"
        head -c 4096 /dev/zero > "$root/$class/user/project/deep/f.bin"
    done
    local dev
    dev="$(dev_of "$root")"
    "$BIN" --threads 1 --prune-home 0 --prune-data 1 --top 10 --out "$TMP/a6.json" \
        --target "$root/home" "$dev" home --target "$root/data" "$dev" data \
        || fail "A6 scan exited non-zero"
    $PY - "$TMP/a6.json" "$root" <<'PYEOF'
import json, sys
mounts = {m["path"]: m for m in json.load(open(sys.argv[1], encoding="utf-8"))["mounts"]}
def mount(cls):
    return mounts[sys.argv[2] + "/" + cls]
def project_children(cls):
    user = next(c for c in mount(cls)["tree"]["children"] if c["name"] == "user")
    project = next(c for c in user["children"] if c["name"] == "project")
    return [c["name"] for c in project.get("children", [])]
assert mount("home")["prune_class"] == "home" and mount("data")["prune_class"] == "data"
assert project_children("data") == [], "--prune-data 1 did not fold the data tree's third level"
assert project_children("home") == ["deep"], (
    "home target emitted %r under project with --prune-home 0" % project_children("home"))
assert mount("home")["tree"]["bytes"] == mount("data")["tree"]["bytes"], "pruning changed rollup bytes"
PYEOF
}
required_case a6_prune_class a6_prune_class

a7_non_utf8_siblings_distinct() {
    local root="$TMP/a7"
    mkdir -p "$root"
    $PY - "$root" <<'PYEOF'
import os, sys
root = os.fsencode(sys.argv[1])
for name in (b"run-\xff", b"run-\xfe"):
    os.mkdir(os.path.join(root, name))
    with open(os.path.join(root, name, b"f.bin"), "wb") as fh:
        fh.write(b"x" * 8192)
PYEOF
    "$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 --out "$TMP/a7.json" \
        --target "$root" "$(dev_of "$root")" data || fail "A7 scan exited non-zero"
    $PY - "$TMP/a7.json" <<'PYEOF'
import base64, json, sys
children = json.load(open(sys.argv[1], encoding="utf-8"))["mounts"][0]["tree"]["children"]
assert len(children) == 2, "expected 2 child directories, got %d" % len(children)
assert [c["name"] for c in children] == ["run-�", "run-�"], [c["name"] for c in children]
raw = sorted(base64.b64decode(c["name_b64"]) for c in children)
assert raw == [b"run-\xfe", b"run-\xff"], raw
PYEOF
}
required_case a7_non_utf8_siblings_distinct a7_non_utf8_siblings_distinct

echo "  expected failures = $XFAIL_COUNT"
[ "$XFAIL_COUNT" -eq 0 ] || fail "expected 0 XFAIL cases, got $XFAIL_COUNT"

echo
echo "ALL TESTS PASSED"
