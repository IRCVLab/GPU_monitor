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

# Pull figures out with python for robust assertions.
read -r SCAN_BYTES BLOCKED_HAS_NOACCESS NODE_NAMES <<EOF
$($PY - "$OUT" "$TREE" <<'PYEOF'
import json,sys
out, tree = sys.argv[1], sys.argv[2]
d = json.load(open(out))
m = d["mounts"][0]
t = m["tree"]
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
