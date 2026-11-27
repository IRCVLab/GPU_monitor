"""The ``truenas`` host profile on the real Nexus mount table, run from cron without systemd."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from agent.storage_viz_agent import config, mount_policy, runner
from agent.storage_viz_agent.block_media import BlockMediaResolver

NEXUS = (Path(__file__).parent / "testdata/truenas/nexus-mountinfo").read_text()
DATASETS = ["/mnt/tank/datasets", "/mnt/tank/workspace"]
# What the setup tool adds: the agent's own dataset, which holds data_dir and run_dir.
AGENT_DATASET = "200 97 0:80 / /mnt/tank/storage-viz rw,noatime shared:400 - zfs tank/storage-viz rw,xattr,posixacl,casesensitive\n"


def select(text, data_dir="/mnt/tank/storage-viz/data", profile="truenas"):
    entries = mount_policy.parse_mountinfo(text)
    return mount_policy.select_scan_roots(entries, root_directory_paths=mount_policy.verified_root_directory_paths(entries),
                                          classify=mount_policy.classifier(profile, entries, data_dir))


def test_truenas_profile_selects_exactly_the_user_datasets_on_nexus():
    assert [root.mountpoint for root in select(NEXUS).selected] == DATASETS


def test_truenas_profile_skips_the_agents_own_dataset():
    result = select(NEXUS + AGENT_DATASET)
    assert [root.mountpoint for root in result.selected] == DATASETS
    assert ("/mnt/tank/storage-viz", "truenas-agent-dataset") in {(s.mountpoint, s.reason) for s in result.skipped}


def test_truenas_profile_names_why_each_system_mount_is_skipped():
    reasons = {s.mountpoint: s.reason for s in select(NEXUS).skipped}
    assert reasons["/"] == reasons["/var/log"] == "truenas-boot-pool"
    assert reasons["/mnt/tank"] == "truenas-pool-root"
    assert reasons["/var/db/system/cores"] == reasons["/var/lib/systemd/coredump"] == "truenas-system-dataset"
    assert reasons["/boot/grub"] == "boot-filesystem"


def test_truenas_profile_requires_a_dataset_under_mnt():
    outside = "300 97 0:90 / /srv/data rw - zfs tank/srv rw\n"
    other_fs = "301 97 259:1 / /mnt/scratch rw - ext4 /dev/nvme0n1p1 rw\n"
    reasons = {s.mountpoint: s.reason for s in select(NEXUS + outside + other_fs).skipped}
    assert reasons["/srv/data"] == "truenas-outside-mnt"
    assert reasons["/mnt/scratch"] == "truenas-not-zfs"


def test_standard_profile_on_nexus_is_unchanged():
    standard = [root.mountpoint for root in select(NEXUS, profile="standard").selected]
    assert "/var/db/system" in standard and "/mnt/tank" in standard and len(standard) > 20


def write_config(tmp, **overrides):
    raw = dict(server_id="nexus", scanner_path="/bin/hstscan", data_dir=str(tmp / "data"), run_dir=str(tmp / "run"),
               host_profile="truenas", volume_aliases={p: "nexus-" + p.rsplit("/", 1)[1] for p in DATASETS},
               volume_media={p: "ssd" for p in DATASETS})
    raw.update(overrides)
    path = tmp / "agent.yaml"
    path.write_text(json.dumps(raw))
    return path


def test_config_rejects_unknown_profile_and_media(tmp_path):
    with pytest.raises(ValueError, match="host_profile"):
        config.load_config(write_config(tmp_path, host_profile="freenas"))
    for media in ({"/mnt/tank/datasets": "nvme"}, {"mnt/tank": "ssd"}, ["/mnt/tank/datasets"]):
        with pytest.raises(ValueError, match="volume_media"):
            config.load_config(write_config(tmp_path, volume_media=media))
    with pytest.raises(ValueError, match="forbidden"):
        config.load_config(write_config(tmp_path, scan_roots=DATASETS))
    cfg = config.load_config(write_config(tmp_path, host_profile="standard", volume_media={}))
    assert (cfg.host_profile, cfg.volume_media) == ("standard", {})


def test_defaults_keep_todays_behaviour(tmp_path):
    path = tmp_path / "agent.yaml"
    path.write_text(json.dumps(dict(server_id="host-a", scanner_path="/bin/hstscan")))
    cfg = config.load_config(path)
    assert (cfg.host_profile, cfg.volume_media) == ("standard", {})


class NoSysfsMedia:
    """sysfs knows no zfs dataset device (major 0): media would be unknown."""

    def resolve(self, device):
        return BlockMediaResolver(Path("/nonexistent")).resolve(device)


def test_zfs_datasets_resolve_by_alias_and_land_in_the_fast_group(tmp_path):
    cfg = config.load_config(write_config(tmp_path, data_dir="/mnt/tank/storage-viz/data"))
    roots, _ = runner.discover(cfg, lambda: NEXUS + AGENT_DATASET, NoSysfsMedia())
    assert [(r.path, r.identity, r.media, r.media_confidence) for r in roots] == [
        (p, "alias", "ssd", "resolved") for p in DATASETS]
    assert set(runner.plan.validate_partition(roots)) == {"fast"}


def test_without_media_override_the_pool_volume_has_unknown_media(tmp_path):
    cfg = config.load_config(write_config(tmp_path, volume_aliases={}, volume_media={}))
    roots, _ = runner.discover(cfg, lambda: NEXUS, NoSysfsMedia())
    # The pool, not an alias, names the volume, so no alias is needed for a stable identity.
    assert {(r.identity, r.media) for r in roots} == {("alias", "unknown")}
    assert len({r.volume_id for r in roots}) == 1


def test_media_override_replaces_only_the_named_mountpoints(tmp_path):
    cfg = config.load_config(write_config(tmp_path, volume_media={"/mnt/tank/workspace": "hdd"}))
    roots, _ = runner.discover(cfg, lambda: NEXUS, NoSysfsMedia())
    assert {r.path: (r.media, r.media_confidence) for r in roots} == {
        "/mnt/tank/datasets": ("unknown", "unresolved"), "/mnt/tank/workspace": ("hdd", "resolved")}


class Scanner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        tail = argv[argv.index("--out") + 2:]
        paths = tail[1::4]
        self.calls.append(paths)
        mounts = [dict(path=p, scanned_bytes=40, scanned_files=2, scanned_dirs=1, errors=0, vanished_dirs=0,
                       tree=dict(name=p, kind="directory", bytes=40, files=2, uid=65534, mtime=1, other_bytes=0))
                  for p in paths]
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(dict(mounts=mounts, users=[], blocked=[], top_files=[], stale=[])))
        return subprocess.CompletedProcess(argv, 0)


def test_cron_runs_every_five_minutes_without_systemd(tmp_path, monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    path = write_config(tmp_path)
    data = tmp_path / "data"
    scanner, now = Scanner(), [100000]

    def run():
        return runner.run_once(path, scheduled=True, mountinfo_reader=lambda: NEXUS,
                               media_resolver=NoSysfsMedia(), scanner_runner=scanner, clock=lambda: now[0])

    assert run() == 0
    assert scanner.calls == [DATASETS]
    first = json.loads((data / "manifest.json").read_bytes())
    sizes, listings, run_ids, outcomes = set(), set(), {first["last_run"]["run_id"]}, []
    for _ in range(36):  # three hours of cron ticks: one fast-cadence rescan, the rest nothing due
        now[0] += 300
        assert run() == 0
        manifest = json.loads((data / "manifest.json").read_bytes())
        run_ids.add(manifest["last_run"]["run_id"])
        outcomes.append((manifest["last_run"]["outcome"], manifest["last_run"]["trigger"]))
        sizes.add(len((data / "manifest.json").read_bytes()))
        listings.add(tuple(sorted(str(p.relative_to(data)) for p in data.rglob("*"))))
        assert not (data / "run.json").exists()
    scans = len(scanner.calls) - 1
    assert scans == (36 * 300) // 3600
    assert sorted(set(outcomes)) == [("complete", "timer"), ("nothing_due", "timer")]
    assert outcomes.count(("nothing_due", "timer")) == 36 - scans
    assert manifest["seq"] > first["seq"] and all(v["capacity"]["status"] for v in manifest["volumes"])
    assert len(run_ids) == 37, "each cron run gets its own run id without INVOCATION_ID"
    assert max(sizes) - min(sizes) < 64, "a nothing-due run must not grow the manifest"
    # manifest.json, captures/, one directory for the pool volume and at most two captures (current and previous).
    assert max(len(listing) for listing in listings) == 2 + 3 * 1
    assert all(len(list(d.iterdir())) == 2 for d in (data / "captures").iterdir())
    assert sorted(p.name for p in (tmp_path / "run").iterdir()) == ["scan.lock"]


def test_rescan_request_is_consumed_by_the_next_cron_run(tmp_path, monkeypatch):
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    path = write_config(tmp_path)
    scanner = Scanner()
    run = lambda: runner.run_once(path, scheduled=True, mountinfo_reader=lambda: NEXUS, media_resolver=NoSysfsMedia(),
                                  scanner_runner=scanner, clock=lambda: 100000)
    run()
    request = "c" * 32
    (tmp_path / "data/requests").mkdir()
    (tmp_path / "data/requests" / request).write_text(request + "\n")
    assert run() == 0
    manifest = json.loads((tmp_path / "data/manifest.json").read_bytes())
    assert [r["request_id"] for r in manifest["requests"]] == [request]
    assert len(scanner.calls) == 2 and not os.listdir(tmp_path / "data/requests")


def test_busy_cron_run_exits_75(tmp_path):
    import fcntl
    path = write_config(tmp_path)
    (tmp_path / "run").mkdir()
    with (tmp_path / "run/scan.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert runner.run_once(path, scheduled=True, mountinfo_reader=lambda: NEXUS, media_resolver=NoSysfsMedia(),
                               scanner_runner=Scanner()) == 75


def test_datasets_of_one_pool_are_roots_of_one_volume(tmp_path):
    # Owner: Nexus is one pool; show /datasets and /workspace inside it, like /home and /data
    # inside the root filesystem on the other servers.
    cfg = config.load_config(write_config(tmp_path, data_dir="/mnt/tank/storage-viz/data"))
    roots, _ = runner.discover(cfg, lambda: NEXUS + AGENT_DATASET, NoSysfsMedia())
    assert [r.path for r in roots] == DATASETS
    assert len({r.volume_id for r in roots}) == 1
    assert len({r.binding for r in roots}) == 2, "each dataset root keeps its own binding"
    volumes = runner.prepare_volumes({"volumes": []}, roots, 100)
    [volume] = volumes.values()
    assert volume["mountpoint"] == "/mnt/tank"
    assert [d["scan_root"] for d in volume["roots"]] == DATASETS
    assert volume["group"] == "fast"


def test_pool_volume_capacity_counts_the_shared_free_space_once(monkeypatch):
    from types import SimpleNamespace
    from agent.storage_viz_agent import capacity
    T = 10 ** 12
    per_root = {"/mnt/tank/datasets": dict(total=13.8 * T, used=4.16 * T, avail=9.64 * T),
                "/mnt/tank/workspace": dict(total=9.65 * T, used=0.01 * T, avail=9.64 * T)}
    monkeypatch.setattr(capacity, "sample", lambda root, previous, now: dict(per_root[root.mountpoint], observed_unix=now, status="ok"))
    roots = [SimpleNamespace(mountpoint=p, volume_mountpoint="/mnt/tank") for p in DATASETS]
    got = capacity.sample_volume(roots, None, 100)
    assert (got["used"], got["avail"], got["total"]) == (4.17 * T, 9.64 * T, 4.17 * T + 9.64 * T)
    plain = [SimpleNamespace(mountpoint="/mnt/tank/datasets", volume_mountpoint=None)]
    assert capacity.sample_volume(plain, None, 100)["total"] == 13.8 * T


def test_standard_profile_keeps_one_volume_per_zfs_dataset(tmp_path):
    cfg = config.load_config(write_config(tmp_path, host_profile="standard"))
    entries = mount_policy.parse_mountinfo(NEXUS)
    from agent.storage_viz_agent import identity
    selected = [s for s in select(NEXUS).selected]
    roots = identity.resolve(selected, {}, cfg.config_digest, NoSysfsMedia(), boot_id="b", devices=[e.major_minor for e in entries])
    assert len({r.volume_id for r in roots}) == 2


def test_display_prefixes_publish_the_paths_clients_see(tmp_path):
    cfg = config.load_config(write_config(tmp_path, data_dir="/mnt/tank/storage-viz/data",
                                          display_prefixes={"/mnt/tank": "/nexus"}))
    roots, _ = runner.discover(cfg, lambda: NEXUS + AGENT_DATASET, NoSysfsMedia())
    assert [r.path for r in roots] == DATASETS, "scanning keeps the real paths"
    [volume] = runner.prepare_volumes({"volumes": []}, roots, 100).values()
    assert volume["mountpoint"] == "/nexus"
    assert [d["scan_root"] for d in volume["roots"]] == ["/nexus/datasets", "/nexus/workspace"]
    from agent.storage_viz_agent import capture
    import base64
    cap = {"v": {"roots": [{"tree": {"name": "/mnt/tank/datasets"},
                            "top_files": [{"path": "/mnt/tank/datasets/NCLT/a.bin"}],
                            "stale": [{"path": "/mnt/tank/datasets/x", "path_b64": base64.b64encode(b"/mnt/tank/datasets/x").decode()}],
                            "errors": []}]}}
    capture.relabel(cap, cfg.display_prefixes)
    root = cap["v"]["roots"][0]
    assert root["tree"]["name"] == "/nexus/datasets"
    assert root["top_files"][0]["path"] == "/nexus/datasets/NCLT/a.bin"
    assert base64.b64decode(root["stale"][0]["path_b64"]) == b"/nexus/datasets/x"
    with pytest.raises(ValueError):
        config.load_config(write_config(tmp_path, display_prefixes={"/": "/nexus"}))
