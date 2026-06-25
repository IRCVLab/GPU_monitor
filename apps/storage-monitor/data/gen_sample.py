#!/usr/bin/env python3
"""Generate a realistic sample fixture for the storage-viz viewer.

Produces hinton.sample.json conforming to schema_version 1. Sizes are in BYTES.
Tree nodes carry other_bytes (sum of pruned small children) so the treemap
stays size-accurate: a node's bytes == sum(child.bytes) + other_bytes.
"""
import json
import random

random.seed(20260624)

GiB = 1024 ** 3
MiB = 1024 ** 2
TiB = 1024 ** 4
KiB = 1024

SCAN_START = 1719200000  # ~2024-06-24, fixed for reproducibility

# uid -> name (lab users)
USERS = {
    0: "root",
    1002: "sungjin",
    1003: "sungoh",
    1004: "donguk",
    1005: "dohyun",
    1006: "jaehyeon",
    1007: "geonyeong",
    1008: "shchoi",
    1009: "jusung",
    1010: "minseo",
}

# Track per-user, per-mount byte tallies as we build the trees.
user_mount = {uid: {} for uid in USERS}


def add_user(uid, mount, nbytes):
    user_mount[uid][mount] = user_mount[uid].get(mount, 0) + nbytes


def node(name, uid, nbytes, files, mtime, other_bytes=0, children=None):
    n = {
        "name": name,
        "bytes": nbytes,
        "files": files,
        "uid": uid,
        "mtime": mtime,
        "other_bytes": other_bytes,
    }
    if children:
        n["children"] = children
    return n


def leaf(name, uid, mount, nbytes, files, mtime):
    """A directory leaf owned by one user; records the user tally."""
    add_user(uid, mount, nbytes)
    return node(name, uid, nbytes, files, mtime)


def mtime(days_ago):
    return SCAN_START - days_ago * 86400


# ---------------------------------------------------------------------------
# Mount "/" (ext4) - OS + a couple of home dirs that didn't get their own mount
# ---------------------------------------------------------------------------
def build_root():
    mount = "/"
    home_children = [
        node("shchoi", 1008, 0, 0, mtime(3), children=[
            leaf(".cache", 1008, mount, 8 * GiB, 40210, mtime(2)),
            leaf("projects", 1008, mount, 22 * GiB, 8800, mtime(1)),
            leaf("miniconda3", 1008, mount, 11 * GiB, 120400, mtime(40)),
        ], other_bytes=900 * MiB),  # dotfiles etc.
        leaf("minseo", 1010, mount, 6 * GiB, 5120, mtime(90)),
    ]
    # fix shchoi home bytes/files
    home_children[0]["bytes"] = 8 * GiB + 22 * GiB + 11 * GiB + 900 * MiB
    home_children[0]["files"] = 40210 + 8800 + 120400 + 220
    add_user(1008, mount, 8 * GiB + 22 * GiB + 11 * GiB + 900 * MiB)

    home = node("home", 0, 0, 0, mtime(1), children=home_children, other_bytes=300 * MiB)
    home["bytes"] = sum(c["bytes"] for c in home_children) + home["other_bytes"]
    home["files"] = sum(c["files"] for c in home_children) + 120
    add_user(0, mount, 300 * MiB)

    usr = leaf("usr", 0, mount, 14 * GiB, 412000, mtime(120))
    usr["other_bytes"] = 0
    var = node("var", 0, 0, 0, mtime(1), children=[
        leaf("log", 0, mount, 3 * GiB, 2200, mtime(0)),
        leaf("lib", 0, mount, 9 * GiB, 88000, mtime(5)),
        leaf("cache", 0, mount, 2 * GiB, 30200, mtime(7)),
    ], other_bytes=600 * MiB)
    var["bytes"] = sum(c["bytes"] for c in var["children"]) + var["other_bytes"]
    var["files"] = sum(c["files"] for c in var["children"]) + 410
    add_user(0, mount, 600 * MiB)

    children = [home, usr, var]
    root_other = 1 * GiB + 512 * MiB  # /bin /etc /opt /tmp small stuff
    add_user(0, mount, root_other + 14 * GiB)  # usr is root too
    tree = node("/", 0, 0, 0, mtime(0), children=children, other_bytes=root_other)
    tree["bytes"] = sum(c["bytes"] for c in children) + root_other
    tree["files"] = sum(c["files"] for c in children) + 9000
    return mount, tree


# ---------------------------------------------------------------------------
# Mount "/data" (xfs) - the big shared research mount
# ---------------------------------------------------------------------------
def build_data():
    mount = "/data"

    def user_dir(name, uid, subdirs, other):
        kids = []
        for (sn, suid, sb, sf, sd) in subdirs:
            kids.append(leaf(sn, suid, mount, sb, sf, mtime(sd)))
        d = node(name, uid, 0, 0, mtime(2), children=kids, other_bytes=other)
        d["bytes"] = sum(c["bytes"] for c in kids) + other
        d["files"] = sum(c["files"] for c in kids) + 1500
        add_user(uid, mount, other)
        return d

    sungjin = user_dir("sungjin", 1002, [
        ("datasets", 1002, 1200 * GiB, 88000, 30),
        ("checkpoints", 1002, 640 * GiB, 4200, 4),
        ("runs", 1002, 210 * GiB, 33000, 1),
    ], other=20 * GiB)

    sungoh = user_dir("sungoh", 1003, [
        ("imagenet", 1003, 480 * GiB, 1300000, 200),
        ("models", 1003, 320 * GiB, 2100, 10),
        ("tmp", 1003, 90 * GiB, 12000, 0),
    ], other=14 * GiB)

    donguk = user_dir("donguk", 1004, [
        ("video_raw", 1004, 900 * GiB, 5400, 60),
        ("features", 1004, 180 * GiB, 240000, 12),
    ], other=11 * GiB)

    dohyun = user_dir("dohyun", 1005, [
        ("nerf_scenes", 1005, 410 * GiB, 64000, 8),
        ("ckpt", 1005, 150 * GiB, 1800, 3),
    ], other=8 * GiB)

    jaehyeon = user_dir("jaehyeon", 1006, [
        ("llm_corpus", 1006, 720 * GiB, 9200, 45),
        ("eval", 1006, 60 * GiB, 51000, 2),
    ], other=7 * GiB)

    shared = node("shared", 0, 0, 0, mtime(15), children=[
        leaf("public_datasets", 0, mount, 1500 * GiB, 410000, 80),
        leaf("conda_envs", 0, mount, 40 * GiB, 980000, 22),
    ], other_bytes=25 * GiB)
    shared["bytes"] = sum(c["bytes"] for c in shared["children"]) + shared["other_bytes"]
    shared["files"] = sum(c["files"] for c in shared["children"]) + 3000
    add_user(0, mount, 25 * GiB)

    children = [sungjin, sungoh, donguk, dohyun, jaehyeon, shared]
    other = 30 * GiB  # lost+found, misc small user dirs
    add_user(0, mount, other)
    tree = node("/data", 0, 0, 0, mtime(0), children=children, other_bytes=other)
    tree["bytes"] = sum(c["bytes"] for c in children) + other
    tree["files"] = sum(c["files"] for c in children) + 200
    return mount, tree


# ---------------------------------------------------------------------------
# Mount "/data1" (ext4) - secondary scratch
# ---------------------------------------------------------------------------
def build_data1():
    mount = "/data1"
    geon = node("geonyeong", 1007, 0, 0, mtime(1), children=[
        leaf("diffusion", 1007, mount, 520 * GiB, 78000, 2),
        leaf("samples", 1007, mount, 130 * GiB, 240000, 0),
    ], other_bytes=9 * GiB)
    geon["bytes"] = sum(c["bytes"] for c in geon["children"]) + geon["other_bytes"]
    geon["files"] = sum(c["files"] for c in geon["children"]) + 800
    add_user(1007, mount, 9 * GiB)

    shchoi = node("shchoi", 1008, 0, 0, mtime(6), children=[
        leaf("backups", 1008, mount, 300 * GiB, 1200, 20),
        leaf("scratch", 1008, mount, 95 * GiB, 56000, 1),
    ], other_bytes=5 * GiB)
    shchoi["bytes"] = sum(c["bytes"] for c in shchoi["children"]) + shchoi["other_bytes"]
    shchoi["files"] = sum(c["files"] for c in shchoi["children"]) + 300
    add_user(1008, mount, 5 * GiB)

    children = [geon, shchoi]
    other = 12 * GiB
    add_user(0, mount, other)
    tree = node("/data1", 0, 0, 0, mtime(0), children=children, other_bytes=other)
    tree["bytes"] = sum(c["bytes"] for c in children) + other
    tree["files"] = sum(c["files"] for c in children) + 100
    return mount, tree


# ---------------------------------------------------------------------------
# Mount "/data3" (xfs) - archive
# ---------------------------------------------------------------------------
def build_data3():
    mount = "/data3"
    archive = node("archive", 0, 0, 0, mtime(80), children=[
        leaf("2023_projects", 0, mount, 2100 * GiB, 520000, 400),
        leaf("2024_projects", 0, mount, 1700 * GiB, 480000, 200),
        leaf("old_checkpoints", 1002, mount, 600 * GiB, 8000, 300),
    ], other_bytes=40 * GiB)
    archive["bytes"] = sum(c["bytes"] for c in archive["children"]) + archive["other_bytes"]
    archive["files"] = sum(c["files"] for c in archive["children"]) + 1200
    add_user(0, mount, 40 * GiB)

    donguk = leaf("donguk_archive", 1004, mount, 380 * GiB, 22000, 150)

    children = [archive, donguk]
    other = 18 * GiB
    add_user(0, mount, other)
    tree = node("/data3", 0, 0, 0, mtime(0), children=children, other_bytes=other)
    tree["bytes"] = sum(c["bytes"] for c in children) + other
    tree["files"] = sum(c["files"] for c in children) + 90
    return mount, tree


# ---------------------------------------------------------------------------
# Assemble mounts with df_* metadata
# ---------------------------------------------------------------------------
def fs_total(scanned, headroom_frac):
    """Pick a df_total a bit larger than scanned so use% is realistic."""
    total = int(scanned / headroom_frac)
    # round up to a tidy TiB-ish number
    return total


def make_mount(path, fstype, builder, headroom):
    _, tree = builder()
    scanned = tree["bytes"]
    files = tree["files"]
    total = fs_total(scanned, headroom)
    # df_used is scanned + some unscanned (reserved blocks, fs overhead, other fs users)
    used = int(scanned * 1.04)
    if used > total:
        used = int(total * 0.97)
    avail = total - used
    use_pct = round(used / total * 100)
    return {
        "path": path,
        "fstype": fstype,
        "df_total": total,
        "df_used": used,
        "df_avail": avail,
        "df_use_pct": use_pct,
        "scanned_bytes": scanned,
        "scanned_files": files,
        "scanned_dirs": random.randint(2000, 60000),
        "errors": random.randint(0, 12),
        "tree": tree,
    }


mounts = [
    make_mount("/", "ext4", build_root, 0.62),
    make_mount("/data", "xfs", build_data, 0.86),
    make_mount("/data1", "ext4", build_data1, 0.74),
    make_mount("/data3", "xfs", build_data3, 0.90),
]

# ---------------------------------------------------------------------------
# users[] aggregated across mounts
# ---------------------------------------------------------------------------
users = []
for uid, by_mount in user_mount.items():
    if not by_mount:
        continue
    total = sum(by_mount.values())
    if total == 0:
        continue
    # rough file count proportional-ish
    users.append({
        "uid": uid,
        "name": USERS[uid],
        "bytes": total,
        "files": int(total / (3 * MiB)) + random.randint(100, 5000),
        "by_mount": dict(sorted(by_mount.items())),
    })
users.sort(key=lambda u: u["bytes"], reverse=True)

# ---------------------------------------------------------------------------
# top_files[] ~30 big files
# ---------------------------------------------------------------------------
TOP_TEMPLATES = [
    ("/data/sungoh/imagenet/imagenet_full.tar", 1003, 210, 240),
    ("/data3/archive/2023_projects/backup_full.img", 0, 380, 410),
    ("/data/donguk/video_raw/session_4k_master.mov", 1004, 95, 61),
    ("/data/jaehyeon/llm_corpus/c4_en.jsonl", 1006, 188, 46),
    ("/data/shared/public_datasets/laion_subset.tar", 0, 320, 82),
    ("/data/sungjin/datasets/pile_dedup.bin", 1002, 260, 31),
    ("/data1/geonyeong/diffusion/sd_xl_weights.safetensors", 1007, 13, 3),
    ("/data/sungjin/checkpoints/run0421_final.pt", 1002, 47, 4),
    ("/data3/archive/old_checkpoints/gpt_pretrain_ep10.pt", 1002, 52, 305),
    ("/data/dohyun/nerf_scenes/city_block_raw.zip", 1005, 41, 9),
    ("/data/sungoh/models/vit_huge.bin", 1003, 11, 11),
    ("/data1/shchoi/backups/home_snapshot_0601.tar.zst", 1008, 120, 21),
    ("/data/donguk/video_raw/drone_flight_8k.mov", 1004, 76, 64),
    ("/data3/archive/2024_projects/dataset_v3.tar", 0, 240, 205),
    ("/data/jaehyeon/llm_corpus/wiki_dump.xml.bz2", 1006, 22, 50),
    ("/data/sungjin/datasets/audio_set.tar", 1002, 95, 33),
    ("/data/shared/conda_envs/env_torch.tar.gz", 0, 6, 22),
    ("/data1/geonyeong/samples/grid_render.mp4", 1007, 8, 1),
    ("/data/dohyun/ckpt/nerf_big_080000.pth", 1005, 9, 4),
    ("/data/sungoh/imagenet/val_high_res.tar", 1003, 64, 201),
    ("/data3/donguk_archive/raw_capture_2023.tar", 1004, 130, 151),
    ("/home/shchoi/miniconda3/pkgs/cuda_toolkit.tar", 1008, 4, 41),
    ("/data/sungjin/runs/tensorboard_events.bin", 1002, 7, 1),
    ("/data/jaehyeon/eval/results_dump.parquet", 1006, 5, 2),
    ("/data1/shchoi/scratch/core.dump", 1008, 18, 1),
    ("/data/donguk/features/clip_feats.npy", 1004, 34, 13),
    ("/data3/archive/2023_projects/logs_archive.tar.gz", 0, 27, 402),
    ("/data/shared/public_datasets/coco2017.zip", 0, 25, 81),
    ("/var/lib/docker/overlay2_blob.img", 0, 9, 5),
    ("/data/sungoh/tmp/scratch_blob.bin", 1003, 40, 0),
]
top_files = []
for path, uid, gib, days in TOP_TEMPLATES:
    top_files.append({
        "path": path,
        "bytes": int(gib * GiB + random.randint(0, 900) * MiB),
        "uid": uid,
        "owner": USERS[uid],
        "mtime": mtime(days),
    })
top_files.sort(key=lambda f: f["bytes"], reverse=True)

# ---------------------------------------------------------------------------
# stale[] ~15 old-but-big files (the "what to delete" view)
# ---------------------------------------------------------------------------
STALE_TEMPLATES = [
    ("/data3/archive/2023_projects/backup_full.img", 0, 380, 410),
    ("/data3/archive/old_checkpoints/gpt_pretrain_ep10.pt", 1002, 52, 305),
    ("/data/sungoh/imagenet/imagenet_full.tar", 1003, 210, 240),
    ("/data3/donguk_archive/raw_capture_2023.tar", 1004, 130, 720),
    ("/data3/archive/2024_projects/dataset_v3.tar", 0, 240, 205),
    ("/data/donguk/video_raw/drone_flight_8k.mov", 1004, 76, 380),
    ("/home/minseo/old_env.tar.gz", 1010, 6, 95),
    ("/data/sungjin/checkpoints/run0119_obsolete.pt", 1002, 47, 188),
    ("/data1/shchoi/scratch/core.dump", 1008, 18, 260),
    ("/data/sungoh/tmp/scratch_blob.bin", 1003, 40, 175),
    ("/data/dohyun/nerf_scenes/abandoned_scene.zip", 1005, 41, 150),
    ("/data3/archive/2023_projects/logs_archive.tar.gz", 0, 27, 402),
    ("/data/jaehyeon/eval/old_results.parquet", 1006, 5, 220),
    ("/data1/geonyeong/samples/grid_render.mp4", 1007, 8, 140),
    ("/var/log/journal_archive.gz", 0, 3, 130),
]
stale = []
for path, uid, gib, days in STALE_TEMPLATES:
    stale.append({
        "path": path,
        "bytes": int(gib * GiB + random.randint(0, 800) * MiB),
        "uid": uid,
        "owner": USERS[uid],
        "mtime": mtime(days),
        "age_days": days,
    })
stale.sort(key=lambda f: f["bytes"], reverse=True)

# ---------------------------------------------------------------------------
# blocked[] - dirs the scanner couldn't enter
# ---------------------------------------------------------------------------
blocked = [
    {"path": "/home/jusung", "reason": "EACCES"},
    {"path": "/data/private_collab", "reason": "EACCES"},
]

doc = {
    "schema_version": 1,
    "hostname": "hinton",
    "scanner_version": "0.1.0",
    "scan_started_unix": SCAN_START,
    "scan_duration_sec": 42.1,
    "run_as_root": True,
    "mounts": mounts,
    "users": users,
    "top_files": top_files,
    "stale": stale,
    "blocked": blocked,
}

out = "/home/shchoi/storage-viz/data/hinton.sample.json"
with open(out, "w") as f:
    json.dump(doc, f, indent=2)

# ---- sanity check: each node bytes == sum(children)+other_bytes ----
def check(n, path="/"):
    kids = n.get("children", [])
    if kids:
        s = sum(c["bytes"] for c in kids) + n.get("other_bytes", 0)
        assert s == n["bytes"], f"MISMATCH at {path}{n['name']}: {s} != {n['bytes']}"
        for c in kids:
            check(c, path + n["name"] + "/")

for m in mounts:
    check(m["tree"])

print(f"Wrote {out}")
print(f"mounts={len(mounts)} users={len(users)} top_files={len(top_files)} stale={len(stale)} blocked={len(blocked)}")
for m in mounts:
    print(f"  {m['path']:8} {m['fstype']:5} scanned={m['scanned_bytes']/TiB:.2f}TiB use={m['df_use_pct']}%")
print("tree byte-consistency: OK")
