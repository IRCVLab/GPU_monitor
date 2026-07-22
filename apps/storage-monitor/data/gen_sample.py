#!/usr/bin/env python3
"""Generate deterministic, privacy-safe sample fixtures for storage-viz.

Produces hinton, atlas, orion, and zeus schema_version 1 snapshots. Sizes are in
bytes. Tree nodes carry other_bytes so each directory's bytes equal
sum(child.bytes) + other_bytes.
"""
from __future__ import annotations

import json
from pathlib import Path
import random
from typing import Any, Callable

GiB = 1024 ** 3
MiB = 1024 ** 2
TiB = 1024 ** 4

SCAN_START = 1719200000
SCAN_DURATION_SEC = 42
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
    1101: "avery",
    1102: "blake",
    1103: "casey",
    1104: "devon",
}


def mtime(days_ago: int, *, start: int = SCAN_START) -> int:
    return start - days_ago * 86400


def capacity_id(major_minor: str) -> str | None:
    """Return canonical dev-major-minor identity or None for invalid input."""
    if not isinstance(major_minor, str) or major_minor.count(":") != 1:
        return None
    major_raw, minor_raw = major_minor.split(":", 1)
    if (
        not major_raw
        or not minor_raw
        or len(major_raw) > 10
        or len(minor_raw) > 10
        or not major_raw.isdigit()
        or not minor_raw.isdigit()
    ):
        return None
    major = int(major_raw)
    minor = int(minor_raw)
    if major <= 0 or (major == 0 and minor == 0):
        return None
    return f"dev-{major}-{minor}"


def add_capacity_identity(record: dict[str, Any], major_minor: str) -> None:
    cid = capacity_id(major_minor)
    if cid is not None:
        record["capacity_id"] = cid


class SnapshotBuilder:
    def __init__(self, server_id: str, *, offset: int = 0) -> None:
        self.server_id = server_id
        self.scan_start = SCAN_START + offset
        self.scan_finished = self.scan_start + SCAN_DURATION_SEC
        self.user_mount: dict[int, dict[str, int]] = {uid: {} for uid in USERS}
        self.blocked: list[dict[str, str]] = []
        self.rng = random.Random(20260624 + offset)

    def add_user(self, uid: int, mount: str, nbytes: int) -> None:
        self.user_mount.setdefault(uid, {})[mount] = self.user_mount.setdefault(uid, {}).get(mount, 0) + nbytes

    def node(self, name: str, uid: int, nbytes: int, files: int, days: int, *, other_bytes: int = 0, children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": name,
            "kind": "directory",
            "bytes": nbytes,
            "files": files,
            "uid": uid,
            "mtime": mtime(days, start=self.scan_start),
            "other_bytes": other_bytes,
        }
        if children:
            out["children"] = children
        return out

    def leaf(self, name: str, uid: int, mount: str, nbytes: int, files: int, days: int) -> dict[str, Any]:
        self.add_user(uid, mount, nbytes)
        return self.node(name, uid, nbytes, files, days)

    def dir_from_children(self, name: str, uid: int, mount: str, children: list[dict[str, Any]], *, other: int, extra_files: int, days: int) -> dict[str, Any]:
        out = self.node(name, uid, 0, 0, days, children=children, other_bytes=other)
        out["bytes"] = sum(c["bytes"] for c in children) + other
        out["files"] = sum(c["files"] for c in children) + extra_files
        self.add_user(uid, mount, other)
        return out

    def selected_root(self, meta: dict[str, Any], mount: dict[str, Any] | None, *, status: str | None = None, error_code: str | None = None) -> dict[str, Any]:
        media = meta["storage_media"]
        confidence = "unresolved" if media == "unknown" else "resolved"
        if mount is None:
            scanned_bytes = scanned_files = scanned_dirs = errors = 0
            status = status or "failed"
        else:
            scanned_bytes = mount["scanned_bytes"]
            scanned_files = mount["scanned_files"]
            scanned_dirs = mount["scanned_dirs"]
            errors = mount["errors"]
            status = status or ("complete" if errors == 0 and self.count_blocked(meta["scan_root"]) == 0 else "partial")
        blocked_count = self.count_blocked(meta["scan_root"])
        record = {
            "mount_id": meta["mount_id"],
            "major_minor": meta["major_minor"],
            "storage_media": media,
            "storage_media_confidence": confidence,
            "mount_source": meta["mount_source"],
            "mount_root": "/",
            "mountpoint": meta["mountpoint"],
            "scan_root": meta["scan_root"],
            "fstype": meta["fstype"],
            "status": status,
            "scanned_bytes": scanned_bytes,
            "scanned_files": scanned_files,
            "scanned_dirs": scanned_dirs,
            "blocked_count": blocked_count,
            "error_count": errors if mount is not None else (1 if status == "failed" else 0),
            "error_code": error_code if error_code is not None else ("EACCES" if blocked_count else ("EIO" if errors else None)),
        }
        add_capacity_identity(record, meta["major_minor"])
        return record

    def count_blocked(self, scan_root: str) -> int:
        prefix = scan_root.rstrip("/") + "/"
        return sum(1 for item in self.blocked if item["path"] == scan_root or item["path"].startswith(prefix))

    def make_mount(self, meta: dict[str, Any], tree: dict[str, Any], *, use_pct: int, errors: int = 0) -> dict[str, Any]:
        scanned = tree["bytes"]
        files = tree["files"]
        used = max(scanned, int(scanned * 1.04))
        total = max(used + 1, int(used * 100 / use_pct))
        avail = total - used
        confidence = "unresolved" if meta["storage_media"] == "unknown" else "resolved"
        record = {
            "path": meta["scan_root"],
            "mount_id": meta["mount_id"],
            "scan_root": meta["scan_root"],
            "fstype": meta["fstype"],
            "storage_media": meta["storage_media"],
            "storage_media_confidence": confidence,
            "df_total": total,
            "df_used": used,
            "df_avail": avail,
            "df_use_pct": use_pct,
            "scanned_bytes": scanned,
            "scanned_files": files,
            "scanned_dirs": self.rng.randint(2000, 60000),
            "errors": errors,
            "tree": tree,
        }
        add_capacity_identity(record, meta["major_minor"])
        return record

    def users(self) -> list[dict[str, Any]]:
        rows = []
        for uid, by_mount in self.user_mount.items():
            if not by_mount:
                continue
            total = sum(by_mount.values())
            if total <= 0:
                continue
            rows.append({
                "uid": uid,
                "name": USERS[uid],
                "bytes": total,
                "files": int(total / (3 * MiB)) + self.rng.randint(100, 5000),
                "by_mount": dict(sorted(by_mount.items())),
            })
        rows.sort(key=lambda u: u["bytes"], reverse=True)
        return rows

    def file_rows(self, templates: list[tuple[str, int, int, int]], *, stale: bool = False) -> list[dict[str, Any]]:
        out = []
        for path, uid, gib, days in templates:
            row = {
                "path": path,
                "kind": "file",
                "bytes": gib * GiB + self.rng.randint(0, 900) * MiB,
                "uid": uid,
                "owner": USERS[uid],
                "mtime": mtime(days, start=self.scan_start),
            }
            if stale:
                row["age_days"] = days
            out.append(row)
        out.sort(key=lambda f: f["bytes"], reverse=True)
        return out

    def doc(self, mounts: list[dict[str, Any]], selected_roots: list[dict[str, Any]], top_files: list[dict[str, Any]], stale: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "hostname": self.server_id,
            "server_id": self.server_id,
            "scanner_version": "0.1.0",
            "scan_started_unix": self.scan_start,
            "scan_finished_unix": self.scan_finished,
            "scan_duration_sec": SCAN_DURATION_SEC,
            "scan_generation": f"{self.server_id}-{self.scan_start}-v1",
            "run_as_root": True,
            "selected_roots": selected_roots,
            "mounts": mounts,
            "users": self.users(),
            "top_files": top_files,
            "stale": stale,
            "blocked": self.blocked,
        }


def hinton_snapshot() -> dict[str, Any]:
    b = SnapshotBuilder("hinton", offset=0)
    b.blocked = [{"path": "/home/jusung", "reason": "EACCES"}, {"path": "/data/private_collab", "reason": "EACCES"}]
    metas = [
        {"mount_id":"rootfs","major_minor":"8:1","mount_source":"/dev/storage-viz/rootfs","mountpoint":"/","scan_root":"/home","fstype":"ext4","storage_media":"ssd"},
        {"mount_id":"data","major_minor":"8:16","mount_source":"/dev/storage-viz/data","mountpoint":"/data","scan_root":"/data","fstype":"xfs","storage_media":"mixed"},
        {"mount_id":"data1","major_minor":"8:32","mount_source":"/dev/storage-viz/data1","mountpoint":"/data1","scan_root":"/data1","fstype":"ext4","storage_media":"ssd"},
        {"mount_id":"data3","major_minor":"8:48","mount_source":"/dev/storage-viz/data3","mountpoint":"/data3","scan_root":"/data3","fstype":"xfs","storage_media":"hdd"},
    ]
    home = b.dir_from_children("/home", 0, "/home", [
        b.dir_from_children("shchoi", 1008, "/home", [b.leaf(".cache",1008,"/home",8*GiB,40210,2), b.leaf("projects",1008,"/home",22*GiB,8800,1), b.leaf("miniconda3",1008,"/home",11*GiB,120400,40)], other=900*MiB, extra_files=220, days=3),
        b.leaf("minseo",1010,"/home",6*GiB,5120,90),
    ], other=300*MiB, extra_files=120, days=1)
    data_children = [
        b.dir_from_children("sungjin",1002,"/data",[b.leaf("datasets",1002,"/data",1200*GiB,88000,30), b.leaf("checkpoints",1002,"/data",640*GiB,4200,4), b.leaf("runs",1002,"/data",210*GiB,33000,1)], other=20*GiB, extra_files=1500, days=2),
        b.dir_from_children("sungoh",1003,"/data",[b.leaf("imagenet",1003,"/data",480*GiB,1300000,200), b.leaf("models",1003,"/data",320*GiB,2100,10), b.leaf("tmp",1003,"/data",90*GiB,12000,0)], other=14*GiB, extra_files=1500, days=2),
        b.dir_from_children("donguk",1004,"/data",[b.leaf("video_raw",1004,"/data",900*GiB,5400,60), b.leaf("features",1004,"/data",180*GiB,240000,12)], other=11*GiB, extra_files=1500, days=2),
        b.dir_from_children("dohyun",1005,"/data",[b.leaf("nerf_scenes",1005,"/data",410*GiB,64000,8), b.leaf("ckpt",1005,"/data",150*GiB,1800,3)], other=8*GiB, extra_files=1500, days=2),
        b.dir_from_children("jaehyeon",1006,"/data",[b.leaf("llm_corpus",1006,"/data",720*GiB,9200,45), b.leaf("eval",1006,"/data",60*GiB,51000,2)], other=7*GiB, extra_files=1500, days=2),
        b.dir_from_children("shared",0,"/data",[b.leaf("public_datasets",0,"/data",1500*GiB,410000,80), b.leaf("conda_envs",0,"/data",40*GiB,980000,22)], other=25*GiB, extra_files=3000, days=15),
    ]
    data = b.dir_from_children("/data",0,"/data",data_children, other=30*GiB, extra_files=200, days=0)
    data1 = b.dir_from_children("/data1",0,"/data1",[
        b.dir_from_children("geonyeong",1007,"/data1",[b.leaf("diffusion",1007,"/data1",520*GiB,78000,2), b.leaf("samples",1007,"/data1",130*GiB,240000,0)], other=9*GiB, extra_files=800, days=1),
        b.dir_from_children("shchoi",1008,"/data1",[b.leaf("backups",1008,"/data1",300*GiB,1200,20), b.leaf("scratch",1008,"/data1",95*GiB,56000,1)], other=5*GiB, extra_files=300, days=6),
    ], other=12*GiB, extra_files=100, days=0)
    data3 = b.dir_from_children("/data3",0,"/data3",[
        b.dir_from_children("archive",0,"/data3",[b.leaf("2023_projects",0,"/data3",2100*GiB,520000,400), b.leaf("2024_projects",0,"/data3",1700*GiB,480000,200), b.leaf("old_checkpoints",1002,"/data3",600*GiB,8000,300)], other=40*GiB, extra_files=1200, days=80),
        b.leaf("donguk_archive",1004,"/data3",380*GiB,22000,150),
    ], other=18*GiB, extra_files=90, days=0)
    mounts = [b.make_mount(metas[0], home, use_pct=64, errors=0), b.make_mount(metas[1], data, use_pct=89, errors=1), b.make_mount(metas[2], data1, use_pct=77, errors=0), b.make_mount(metas[3], data3, use_pct=94, errors=2)]
    roots = [b.selected_root(meta, mount) for meta, mount in zip(metas, mounts)]
    top = b.file_rows([
        ("/data3/archive/2023_projects/backup_full.img", 0, 380, 410),
        ("/data/shared/public_datasets/laion_subset.tar", 0, 320, 82),
        ("/data/sungjin/datasets/pile_dedup.bin", 1002, 260, 31),
        ("/data3/archive/2024_projects/dataset_v3.tar", 0, 240, 205),
        ("/data/sungoh/imagenet/imagenet_full.tar", 1003, 210, 240),
        ("/data/jaehyeon/llm_corpus/c4_en.jsonl", 1006, 188, 46),
        ("/data3/donguk_archive/raw_capture_2023.tar", 1004, 130, 151),
        ("/data1/shchoi/backups/home_snapshot_0601.tar.zst", 1008, 120, 21),
        ("/data/sungjin/datasets/audio_set.tar", 1002, 95, 33),
        ("/data/donguk/video_raw/session_4k_master.mov", 1004, 95, 61),
        ("/data/donguk/video_raw/drone_flight_8k.mov", 1004, 76, 64),
        ("/data/sungoh/imagenet/val_high_res.tar", 1003, 64, 201),
        ("/data3/archive/old_checkpoints/gpt_pretrain_ep10.pt", 1002, 52, 305),
        ("/data/sungjin/checkpoints/run0421_final.pt", 1002, 47, 4),
        ("/data/dohyun/nerf_scenes/city_block_raw.zip", 1005, 41, 9),
        ("/data/sungoh/tmp/scratch_blob.bin", 1003, 40, 0),
        ("/data/donguk/features/clip_feats.npy", 1004, 34, 13),
        ("/data3/archive/2023_projects/logs_archive.tar.gz", 0, 27, 402),
        ("/data/shared/public_datasets/coco2017.zip", 0, 25, 81),
        ("/data/jaehyeon/llm_corpus/wiki_dump.xml.bz2", 1006, 22, 50),
        ("/data1/shchoi/scratch/core.dump", 1008, 18, 1),
        ("/data1/geonyeong/diffusion/sd_xl_weights.safetensors", 1007, 13, 3),
        ("/data/sungoh/models/vit_huge.bin", 1003, 11, 11),
        ("/data/dohyun/ckpt/nerf_big_080000.pth", 1005, 9, 4),
        ("/data1/geonyeong/samples/grid_render.mp4", 1007, 8, 1),
        ("/data/sungjin/runs/tensorboard_events.bin", 1002, 7, 1),
        ("/data/shared/conda_envs/env_torch.tar.gz", 0, 6, 22),
        ("/data/jaehyeon/eval/results_dump.parquet", 1006, 5, 2),
        ("/home/shchoi/miniconda3/pkgs/cuda_toolkit.tar", 1008, 4, 41),
    ])
    stale = b.file_rows([
        ("/data3/archive/2023_projects/backup_full.img", 0, 380, 410),
        ("/data3/archive/2024_projects/dataset_v3.tar", 0, 240, 205),
        ("/data/sungoh/imagenet/imagenet_full.tar", 1003, 210, 240),
        ("/data3/donguk_archive/raw_capture_2023.tar", 1004, 130, 720),
        ("/data/donguk/video_raw/drone_flight_8k.mov", 1004, 76, 380),
        ("/data3/archive/old_checkpoints/gpt_pretrain_ep10.pt", 1002, 52, 305),
        ("/data/sungjin/checkpoints/run0119_obsolete.pt", 1002, 47, 188),
        ("/data/dohyun/nerf_scenes/abandoned_scene.zip", 1005, 41, 150),
        ("/data/sungoh/tmp/scratch_blob.bin", 1003, 40, 175),
        ("/data3/archive/2023_projects/logs_archive.tar.gz", 0, 27, 402),
        ("/data1/shchoi/scratch/core.dump", 1008, 18, 260),
        ("/data1/geonyeong/samples/grid_render.mp4", 1007, 8, 140),
        ("/home/minseo/old_env.tar.gz", 1010, 6, 95),
        ("/data/jaehyeon/eval/old_results.parquet", 1006, 5, 220),
        ("/home/minseo/journal_archive.gz", 1010, 3, 130),
    ], stale=True)
    return b.doc(mounts, roots, top, stale)


def simple_snapshot(server_id: str, *, offset: int, medias: list[str], use_pcts: list[int], failed_unknown: bool = False) -> dict[str, Any]:
    b = SnapshotBuilder(server_id, offset=offset)
    metas = []
    mounts = []
    for i, media in enumerate(medias, start=1):
        root = f"/srv/{server_id}/vol{i}"
        meta = {"mount_id":f"vol{i}","major_minor":f"9:{offset+i}","mount_source":f"/dev/storage-viz/{server_id}-vol{i}","mountpoint":root,"scan_root":root,"fstype":"xfs","storage_media":media}
        metas.append(meta)
        tree = b.dir_from_children(root,0,root,[
            b.dir_from_children("projects",1101,root,[b.leaf("dataset",1101,root,(120*i+offset)*GiB,8000*i,14+i), b.leaf("models",1102,root,(35*i+offset)*GiB,900*i,4+i)], other=(2*i)*GiB, extra_files=90*i, days=1),
            b.leaf("scratch",1103,root,(15*i+offset)*GiB,1200*i,2),
        ], other=GiB*i, extra_files=40*i, days=0)
        mounts.append(b.make_mount(meta, tree, use_pct=use_pcts[i-1], errors=0 if media != "unknown" else 1))
    roots = [b.selected_root(meta, mount) for meta, mount in zip(metas, mounts)]
    if failed_unknown:
        failed = {"mount_id":"unresolved","major_minor":f"9:{offset+99}","mount_source":f"/dev/storage-viz/{server_id}-unresolved","mountpoint":f"/srv/{server_id}/lost","scan_root":f"/srv/{server_id}/lost","fstype":"xfs","storage_media":"unknown"}
        roots.append(b.selected_root(failed, None, status="failed", error_code="ENODEV"))
    top_templates = []
    for idx in range(10):
        volume = idx % len(mounts) + 1
        owner = [1101, 1102, 1103, 1104][idx % 4]
        subdir = ["dataset", "models", "scratch", "reports"][idx % 4]
        top_templates.append((
            f"/srv/{server_id}/vol{volume}/projects/{subdir}/{server_id}-{idx:02d}.bin",
            owner,
            offset + 45 - idx * 3,
            20 + idx * 11,
        ))
    stale_templates = []
    for idx in range(5):
        volume = idx % len(mounts) + 1
        owner = [1101, 1102, 1103, 1104][idx % 4]
        stale_templates.append((
            f"/srv/{server_id}/vol{volume}/projects/archive/old-{server_id}-{idx:02d}.tar",
            owner,
            offset + 36 - idx * 4,
            180 + idx * 35,
        ))
    top = b.file_rows(top_templates)
    stale = b.file_rows(stale_templates, stale=True)
    return b.doc(mounts, roots, top, stale)


SNAPSHOTS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("hinton", hinton_snapshot),
    ("atlas", lambda: simple_snapshot("atlas", offset=100, medias=["ssd", "ssd"], use_pcts=[10, 82])),
    ("orion", lambda: simple_snapshot("orion", offset=200, medias=["hdd", "hdd"], use_pcts=[93, 70])),
    ("zeus", lambda: simple_snapshot("zeus", offset=300, medias=["mixed", "unknown"], use_pcts=[88, 96], failed_unknown=True)),
]


def check_tree(node: dict[str, Any], path: str = "/") -> None:
    children = node.get("children", [])
    if children:
        total = sum(child["bytes"] for child in children) + node.get("other_bytes", 0)
        assert total == node["bytes"], f"MISMATCH at {path}{node['name']}: {total} != {node['bytes']}"
        for child in children:
            check_tree(child, path + node["name"].strip("/") + "/")
    else:
        assert node.get("other_bytes", 0) == 0, f"leaf has other_bytes at {path}{node['name']}"


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    docs = []
    for sid, factory in SNAPSHOTS:
        doc = factory()
        for mount in doc["mounts"]:
            check_tree(mount["tree"])
        out = out_dir / f"{sid}.sample.json"
        out.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        docs.append(doc)
        print(f"Wrote {out}")
    print("mounts=" + ",".join(f"{d['server_id']}:{len(d['mounts'])}" for d in docs))
    print("tree byte-consistency: OK")


if __name__ == "__main__":
    main()
