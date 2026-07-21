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

TMP="$(mktemp -d /tmp/hstscan_test.XXXXXX)"
OUT="$(mktemp /tmp/hstscan_out.XXXXXX.json)"
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

GUARDED_OUT="$(mktemp /tmp/hstscan_guarded.XXXXXX.json)"
"$BIN" --threads 2 --prune-home 0 --prune-data 0 --top 10 --out "$GUARDED_OUT" --target "$TREE" "$TREE_DEV"
GRC=$?
[ "$GRC" -eq 0 ] || fail "guarded --target scan exited non-zero ($GRC)"
$PY - "$GUARDED_OUT" "$TREE" <<'PYEOF' || fail "guarded --target did not scan the exact requested root"
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert [m["path"] for m in payload["mounts"]] == [sys.argv[2]]
PYEOF
rm -f "$GUARDED_OUT" "$GUARDED_OUT.tmp"
pass "guarded --target PATH MAJOR:MINOR scans exact absolute target"

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
SAMEDEV_OUT="$(mktemp /tmp/hstscan_samedev_hardlink.XXXXXX.json)"
"$BIN" --threads 1 --prune-home 0 --prune-data 0 --top 10 --out "$SAMEDEV_OUT" \
    --target "$SAMEDEV_HOME" "$SAMEDEV_DEV" \
    --target "$SAMEDEV_DATA" "$SAMEDEV_DEV"
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

MISMATCH_OUT="$(mktemp /tmp/hstscan_mismatch.XXXXXX.json)"
if [ "$TREE_DEV" = "0:0" ]; then WRONG_DEV="1:0"; else WRONG_DEV="0:0"; fi
"$BIN" --threads 2 --prune-home 0 --prune-data 0 --top 10 --out "$MISMATCH_OUT" --target "$TREE" "$WRONG_DEV"
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
    "--target relative 1:2" \
    "--target $TREE" \
    "--target $TREE 1" \
    "--target $TREE 1:" \
    "--target $TREE :2" \
    "--target $TREE +1:2" \
    "--target $TREE -1:2" \
    "--target $TREE 1:-2" \
    "--target $TREE 1:2x" \
    "--target $TREE 999999999999999999999999999999:0" \
    "--target $TREE $TREE_DEV $TREE"; do
    # shellcheck disable=SC2086
    if "$BIN" --out "$TMP/invalid.json" $args >/dev/null 2>"$TMP/invalid.err"; then
        IRC=0
    else
        IRC=$?
    fi
    [ "$IRC" -eq 2 ] || fail "invalid guarded args [$args] exited $IRC, expected 2"
done
pass "guarded --target parser rejects malformed, non-absolute, and mixed positional targets"

TOO_MANY_GUARDED=()
for n in $(seq 1 65); do
    TOO_MANY_GUARDED+=(--target "$TREE" "$TREE_DEV")
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

$PY - "$SCRIPT_DIR/hstscan.c" <<'PYEOF' || fail "source-order contract violated: process_dir must fstat dirfd before getdents64"
import re, sys
s = open(sys.argv[1], encoding="utf-8").read()
start = s.find("static void process_dir(")
end = s.find("static void *worker_main(", start)
if start == -1 or end == -1:
    raise SystemExit("process_dir bounds not found")
body = s[start:end]
open_i = body.find("open(")
fstat_i = body.find("fstat(dirfd")
getdents_i = body.find("SYS_getdents64")
mismatch_i = body.find("dst.st_dev != c->root_dev")
blocked_i = body.find("blocked_add", mismatch_i)
close_i = body.find("close(dirfd)", mismatch_i)
if not (open_i != -1 and fstat_i != -1 and getdents_i != -1 and open_i < fstat_i < getdents_i):
    raise SystemExit(f"open={open_i} fstat={fstat_i} getdents={getdents_i}")
if not (mismatch_i != -1 and blocked_i != -1 and close_i != -1 and mismatch_i < blocked_i < close_i):
    raise SystemExit("process_dir device mismatch must increment visible blocked/error state before close")
PYEOF
pass "source-order contract fstat(dirfd) before getdents64 is present"

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
if "--target PATH MAJOR:MINOR" not in s or "Do not mix" not in s:
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
WIDE_OUT="$(mktemp /tmp/hstscan_wide.XXXXXX.json)"
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

echo
echo "ALL TESTS PASSED"
