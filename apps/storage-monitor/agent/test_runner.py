"""Agent bug repros A3, A4, A6, A8 and A9 from docs/redesign/bug-catalog.md.

Each test asserts the behaviour the redesign promises. A3, A4, A8 and A9 are
strict expected failures until the unit that fixes them (M5) removes the marker.
"""
import json
import os
import pathlib

import pytest

from agent import scan_runner

HOUR = 3600
SSD, HDD = "8:1", "8:2"


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Resolver:
    def resolve(self, device):
        media = {SSD: "ssd", HDD: "hdd"}[device]
        return scan_runner.MediaResult("dev-" + device.replace(":", "-"), media, "resolved")


def mountinfo_line(mount_id, parent_id, device, mountpoint, fstype, source):
    return "%d %d %s / %s rw - %s %s rw" % (mount_id, parent_id, device, mountpoint, fstype, source)


def ssd_root_hdd_data(root_id=1, data_id=2):
    """`/` on an SSD (scanned as /home) and /data on an HDD."""
    return "\n".join((
        mountinfo_line(root_id, 0, SSD, "/", "ext4", "/dev/sda1"),
        mountinfo_line(data_id, root_id, HDD, "/data", "xfs", "/dev/sdb1"),
    ))


def write_config(tmp):
    path = tmp / "scanner.yaml"
    path.write_text(json.dumps({
        "server_id": "host-a",
        "scanner_path": "/bin/hstscan",
        "data_dir": str(tmp / "data"),
        "run_dir": str(tmp / "run"),
    }))
    return path


def df_fields(path):
    """Capacity figures the way hstscan's fill_df computes them."""
    vfs = os.statvfs(path)
    total = vfs.f_blocks * vfs.f_frsize
    used = total - vfs.f_bfree * vfs.f_frsize
    avail = vfs.f_bavail * vfs.f_frsize
    return {"df_total": total, "df_used": used, "df_avail": avail,
            "df_use_pct": -(-used * 100 // (used + avail)) if used + avail else 0}


def raw_mount(path, errors=0, capacity=None):
    mount = {
        "path": path, "fstype": "ext4",
        "df_total": 1000, "df_used": 400, "df_avail": 600, "df_use_pct": 40,
        "scanned_bytes": 123, "scanned_files": 4, "scanned_dirs": 2, "errors": errors,
        "tree": {"name": path, "kind": "directory", "bytes": 123, "files": 4,
                 "uid": 0, "mtime": 99, "other_bytes": 0},
    }
    mount.update(capacity or {})
    return mount


class FakeScanner:
    """Stands in for hstscan: records each run's targets and writes its JSON."""

    def __init__(self, errors=None, exit_codes=None, capacity_from_statvfs=False):
        self.calls = []
        self.prune_classes = []
        self.errors = errors or {}
        self.exit_codes = exit_codes or {}
        self.capacity_from_statvfs = capacity_from_statvfs
        self.started_unix = 0

    def __call__(self, argv, **kwargs):
        paths = argv[argv.index("--out") + 3::4]
        self.calls.append(tuple(paths))
        self.prune_classes.append(tuple(argv[argv.index("--out") + 5::4]))
        for path in paths:
            if self.exit_codes.get(path):
                return scan_runner.CompletedScan(self.exit_codes[path], stderr="hstscan: out of memory")
        mounts = [raw_mount(path, self.errors.get(path, 0),
                            df_fields(path) if self.capacity_from_statvfs else None)
                  for path in paths]
        raw = {
            "schema_version": 1, "hostname": "host-a", "scanner_version": "0.1.0",
            "scan_started_unix": self.started_unix, "scan_duration_sec": 1.0,
            "run_as_root": False, "mounts": mounts, "users": [], "top_files": [],
            "stale": [], "blocked": [],
        }
        pathlib.Path(argv[argv.index("--out") + 1]).write_text(json.dumps(raw))
        return scan_runner.CompletedScan(0)


def run(config, mountinfo, scanner, now, scheduled=False):
    scanner.started_unix = now - 30
    return scan_runner.run_once(
        config, scheduled=scheduled, mountinfo_reader=lambda: mountinfo,
        scanner_runner=scanner, media_resolver=Resolver(), clock=Clock(now),
    )


def published_snapshot(tmp):
    """The snapshot scan-status.json points at, or None if nothing was published."""
    try:
        status = json.loads((tmp / "data" / "scan-status.json").read_text())
        return json.loads((tmp / "data" / "snapshots" / status["generation"]).read_text())
    except FileNotFoundError:
        return None


def test_a6_home_mount_gets_home_class(tmp_path):
    config = write_config(tmp_path)
    separate_home = "\n".join((
        mountinfo_line(1, 0, SSD, "/", "ext4", "/dev/sda1"),
        mountinfo_line(2, 1, HDD, "/home", "xfs", "/dev/sdb1"),
    ))

    root_backed = FakeScanner()
    run(config, ssd_root_hdd_data(), root_backed, now=5 * HOUR + 30)
    mounted = FakeScanner()
    run(config, separate_home, mounted, now=5 * HOUR + 30)

    assert list(zip(root_backed.calls, root_backed.prune_classes)) == [
        (("/home",), ("home",)), (("/data",), ("data",))]
    assert list(zip(mounted.calls, mounted.prune_classes)) == [(("/home",), ("home",))]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A3: a run where no root scans cleanly publishes nothing")
def test_a3_single_partial_volume_publishes(tmp_path):
    config = write_config(tmp_path)
    single_root = mountinfo_line(1, 0, SSD, "/", "ext4", "/dev/sda1")

    result = run(config, single_root, FakeScanner(errors={"/home": 1}), now=5 * HOUR + 30)

    snapshot = published_snapshot(tmp_path)
    assert snapshot is not None, (
        "A3: single root with one scan error published nothing (run status %r, error %r)"
        % (result.status, result.error))
    assert [mount["path"] for mount in snapshot["mounts"]] == ["/home"]
    assert result.status == "partial"


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A4: a failed HDD group discards the good SSD capture from the same run")
def test_a4_hdd_failure_keeps_ssd_capture(tmp_path):
    config = write_config(tmp_path)
    scanner = FakeScanner(exit_codes={"/data": 2})

    result = run(config, ssd_root_hdd_data(), scanner, now=5 * HOUR + 30)

    assert scanner.calls == [("/home",), ("/data",)]
    snapshot = published_snapshot(tmp_path)
    assert snapshot is not None, (
        "A4: SSD /home scanned cleanly but the HDD failure published nothing (run status %r, error %r)"
        % (result.status, result.error))
    assert "/home" in [mount["path"] for mount in snapshot["mounts"]]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A8: the lifecycle keeps phase 'publishing' after the run completed")
def test_a8_no_publishing_phase_after_completion(tmp_path):
    config = write_config(tmp_path)

    result = run(config, ssd_root_hdd_data(), FakeScanner(), now=5 * HOUR + 30)

    assert result.status == "complete"
    lifecycle = json.loads((tmp_path / "data" / "scan-lifecycle.json").read_text())
    assert lifecycle["state"] == "complete"
    assert lifecycle.get("phase") not in {"preparing", "scanning", "publishing"}, (
        "A8: lifecycle state is %r but phase is still %r" % (lifecycle["state"], lifecycle["phase"]))


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A9: HDD capacity is reused from the cached slow capture, up to 3 h old")
def test_a9_capacity_fresh_on_slow_only_not_due_tick(tmp_path, monkeypatch):
    real_statvfs = os.statvfs
    used_blocks = {"/home": 100, "/data": 400}

    def fake_statvfs(path):
        if path not in used_blocks:
            return real_statvfs(path)
        # f_bsize, f_frsize, f_blocks, f_bfree, f_bavail, f_files, f_ffree, f_favail, f_flag, f_namemax
        return os.statvfs_result((4096, 4096, 1000, 1000 - used_blocks[path],
                                  1000 - used_blocks[path], 0, 0, 0, 0, 255))

    monkeypatch.setattr(os, "statvfs", fake_statvfs)
    config = write_config(tmp_path)
    scanner = FakeScanner(capacity_from_statvfs=True)
    run(config, ssd_root_hdd_data(), scanner, now=5 * HOUR + 30, scheduled=True)

    used_blocks["/data"] = 900  # someone fills /data before the next hourly tick
    result = run(config, ssd_root_hdd_data(), scanner, now=6 * HOUR + 30, scheduled=True)

    assert scanner.calls == [("/home",), ("/data",), ("/home",)], "the HDD tree must be reused on this tick"
    assert result.status == "complete"
    data_mount = next(m for m in published_snapshot(tmp_path)["mounts"] if m["path"] == "/data")
    assert data_mount["df_used"] == 900 * 4096, (
        "A9: /data df_used is %d bytes from the cached capture; statvfs now reports %d"
        % (data_mount["df_used"], 900 * 4096))
