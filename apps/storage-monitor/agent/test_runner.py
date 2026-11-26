import gzip
import json
import os
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace

from agent.storage_viz_agent import capacity, runner
from tests.machine_check import assert_table_total, run_random_events


def line(device, path, mount_id=1, subtree="/"):
    return "%d 0 %s %s %s rw - ext4 /dev/disk rw" % (mount_id, device, subtree, path)


class Resolver:
    def resolve(self, device):
        return SimpleNamespace(media="ssd" if device == "8:1" else "hdd", confidence="resolved")


class Host:
    def __init__(self, tmp):
        tmp.mkdir(parents=True, exist_ok=True)
        self.data = tmp / "data"
        self.runtime = tmp / "run"
        self.config = tmp / "config"
        self.config.write_text(json.dumps(dict(server_id="host-a", scanner_path="/bin/hstscan",
            run_dir=str(self.runtime), data_dir=str(self.data), volume_aliases={"/home":"root", "/data":"disk"})))
        self.mounts = line("8:1", "/") + "\n" + line("8:2", "/data", 2)
        self.calls = []
        self.failed = set()
        self.errors = 0
        self.now = 20000

    def scan(self, argv):
        tail = argv[argv.index("--out")+2:]
        paths = tail[1::4]
        self.calls.append(list(zip(paths, tail[3::4])))
        if self.failed.intersection(paths):
            return subprocess.CompletedProcess(argv, 1)
        mounts = [dict(path=p, scanned_bytes=40, scanned_files=2, scanned_dirs=1, errors=self.errors,
                       vanished_dirs=self.errors, tree=dict(name=p, kind="directory", bytes=40, files=2,
                                                            uid=0, mtime=1, other_bytes=0)) for p in paths]
        Path(argv[argv.index("--out")+1]).write_text(json.dumps(dict(mounts=mounts, users=[], blocked=[],
                                                                    top_files=[], stale=[])))
        return subprocess.CompletedProcess(argv, 0)

    def run(self, **kwargs):
        return runner.run_once(self.config, mountinfo_reader=lambda: self.mounts, media_resolver=Resolver(),
                               clock=lambda: self.now, scanner_runner=self.scan, **kwargs)

    def manifest(self):
        return json.loads((self.data / "manifest.json").read_bytes())

    def volumes(self):
        return {v["roots"][0]["scan_root"]: v for v in self.manifest()["volumes"]}

    def request(self, request="a"*32):
        directory = self.data / "requests"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / request).write_text(request + "\n")


def test_a6_home_mount_gets_home_class(tmp_path):
    host = Host(tmp_path)
    assert host.run() == 0
    assert host.volumes()["/home"]["mountpoint"] == "/"
    assert host.calls == [[("/home", "home")], [("/data", "data")]]
    host.mounts = line("8:1", "/") + "\n" + line("8:2", "/home", 2)
    assert host.run() == 0
    assert host.calls[-1] == [("/home", "home")]


def test_a3_single_partial_volume_publishes(tmp_path):
    host = Host(tmp_path)
    host.mounts = line("8:1", "/")
    host.errors = 1
    assert host.run() == 0
    v = host.volumes()["/home"]
    assert v["last_attempt"]["outcome"] == v["capture"]["outcome"] == "partial"
    path = host.data / "captures" / v["volume_id"] / (v["capture"]["sha256"] + ".json.gz")
    assert json.loads(gzip.decompress(path.read_bytes()))["roots"][0]["counts"]["errors"] == 1


def test_a3_all_failed_records_attempts(tmp_path):
    host = Host(tmp_path)
    host.failed.update(("/home", "/data"))
    assert host.run() == 1
    assert all(v["capture"] is None and v["last_attempt"]["outcome"] == "failed" for v in host.volumes().values())


def test_a4_hdd_failure_keeps_ssd_capture(tmp_path):
    host = Host(tmp_path)
    host.run()
    old = host.volumes()
    host.failed.add("/data")
    host.now += 100
    assert host.run() == 0
    volumes = host.volumes()
    assert volumes["/home"]["capture"] != old["/home"]["capture"]
    assert volumes["/data"]["capture"] == old["/data"]["capture"]
    assert volumes["/data"]["last_attempt"]["outcome"] == "failed"
    assert host.manifest()["last_run"]["outcome"] == "partial"


def test_a8_no_run_json_after_completion(tmp_path):
    host = Host(tmp_path)
    host.data.mkdir()
    for name in ("scan-status.json", "scan-lifecycle.json", "slow-capture.json.gz", "snapshots/old"):
        p = host.data / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"rollback")
    assert host.run() == 0
    assert not (host.data / "run.json").exists()
    assert '"phase"' not in json.dumps(host.manifest())
    assert all(p.read_bytes() == b"rollback" for p in (host.data/"snapshots/old", host.data/"scan-status.json",
               host.data/"scan-lifecycle.json", host.data/"slow-capture.json.gz"))


def test_a9_capacity_fresh_on_slow_only_not_due_tick(tmp_path, monkeypatch):
    host = Host(tmp_path)
    root = tmp_path / "slow"
    root.mkdir()
    device = root.stat().st_dev
    host.mounts = line("%d:%d" % (os.major(device), os.minor(device)), str(root))
    config = json.loads(host.config.read_text())
    config["volume_aliases"] = {str(root): "disk"}
    host.config.write_text(json.dumps(config))
    assert host.run(scheduled=True) == 0
    host.now += 100
    monkeypatch.setattr(os, "fstatvfs", lambda fd: os.statvfs_result((4096,4096,1000,100,80,0,0,0,0,255)))
    assert host.run(scheduled=True) == 0
    sample = host.volumes()[str(root)]["capacity"]
    assert len(host.calls) == 1 and host.manifest()["last_run"]["outcome"] == "nothing_due"
    assert sample["observed_unix"] == host.now and sample["used"] == 900 * 4096
    assert capacity.sample(SimpleNamespace(mountpoint=str(root), device="9:99"), sample, host.now+99) == dict(sample,status="failed")


def test_c4_request_served_after_running_scan(tmp_path):
    host = Host(tmp_path)
    def queued(step):
        if step == "capture" and len(host.calls) == 1:
            host.request()
            assert host.run() == 75
            assert (host.data / "requests" / ("a"*32)).is_file()
    assert host.run(scheduled=True, persisted=queued) == 0
    assert host.calls == [[("/home","home")], [("/data","data")]] * 2
    receipt, = host.manifest()["requests"]
    assert receipt["request_id"] == "a"*32 and receipt["outcome"] == "complete"
    host.request()
    host.run(scheduled=True)
    assert len(host.calls) == 4 and host.manifest()["requests"] == [receipt]


def test_run_table_total():
    assert_table_total(runner.TRANSITIONS, runner.STATES, runner.EVENTS)


def test_run_random_events():
    def check(before, event, after, effects):
        assert after in runner.STATES
        if after == "idle" and before != "idle":
            assert event in ("empty", "finish")
        if event == "group_done" and before == "scanning":
            assert after == "scanning"
        if before == "recovering" and after != before:
            assert event == "recovered"
    run_random_events(lambda rng: "idle", lambda rng, state, idx: rng.choice(runner.EVENTS),
                      lambda state, event: (runner.transition(state, event), ()), check)


def test_kill_after_each_persist_step(tmp_path):
    probe = Host(tmp_path / "probe")
    probe.request()
    steps = []
    probe.run(persisted=steps.append)
    for index in range(len(steps)):
        host = Host(tmp_path / str(index))
        host.request()
        pid = os.fork()
        if pid == 0:
            def kill(step):
                if len(log) == index:
                    os.kill(os.getpid(), signal.SIGKILL)
                log.append(step)
            log = []
            host.run(persisted=kill)
            os._exit(1)
        assert os.waitpid(pid, 0)[1] == signal.SIGKILL
        journal_path = host.data / "run.json"
        stale = json.loads(journal_path.read_bytes()) if journal_path.exists() else None
        previous = host.manifest() if (host.data / "manifest.json").exists() else {}
        snapshots = []
        def inspect(step):
            if step == "manifest":
                snapshots.append(host.manifest())
        assert host.run(scheduled=True, persisted=inspect) == 0
        assert not (host.data / "run.json").exists()
        receipt, = host.manifest()["requests"]
        if stale and not previous.get("last_run"):
            recovered = snapshots[0]
            assert recovered["last_run"]["outcome"] == "interrupted"
            assert recovered["last_run"]["run_id"] == stale["run_id"]
            planned = {vid for group in stale["plan"] for vid in group["volumes"]}
            for old, new in zip(previous.get("volumes", []), recovered["volumes"]):
                assert old["capture"] == new["capture"]
                if old["volume_id"] in planned and old["last_attempt"] is None:
                    assert new["last_attempt"]["outcome"] == "interrupted"
            assert receipt["outcome"] == ("interrupted" if stale["request_ids"] else "complete")
        else:
            assert receipt["outcome"] == "complete"
        assert all(m["seq"] == snapshots[0]["seq"] + n for n, m in enumerate(snapshots))
        assert all(v["capture"] for v in host.volumes().values())


def test_only_valid_request_files_are_consumed(tmp_path):
    host = Host(tmp_path)
    host.request()
    pending = host.data / "requests"
    (pending / ("b"*32)).symlink_to(pending / ("a"*32))
    os.mkfifo(pending / ("c"*32))
    (pending / ("d"*32)).write_bytes(b"d"*65)
    (pending / ("e"*32)).write_bytes(b"f"*32)
    assert host.run() == 0
    assert [r["request_id"] for r in host.manifest()["requests"]] == ["a"*32]
    assert len(list(pending.iterdir())) == 4


def test_retains_two_captures_and_last_sixteen_receipts(tmp_path):
    host = Host(tmp_path)
    for n in range(18):
        host.request("%032x" % n)
        host.now += 1
        host.run(scheduled=True)
    assert [r["request_id"] for r in host.manifest()["requests"]] == ["%032x" % n for n in range(2,18)]
    for v in host.volumes().values():
        directory = host.data / "captures" / v["volume_id"]
        assert len(list(directory.iterdir())) == 2
        assert (directory / (v["capture"]["sha256"] + ".json.gz")).is_file()


def test_request_arrives_as_runner_removes_journal(tmp_path):
    host = Host(tmp_path)
    def request_after_clear(step):
        if step == "clear" and len(host.calls) == 2:
            host.request()
    assert host.run(scheduled=True, persisted=request_after_clear) == 0
    assert len(host.calls) == 4
    assert host.manifest()["requests"][0]["outcome"] == "complete"


def test_bad_scanner_rows_fail_group_and_preserve_capture(tmp_path):
    host = Host(tmp_path)
    host.run()
    prior = host.volumes()["/data"]["capture"]
    original = host.scan
    def malformed(argv):
        result = original(argv)
        if "/data" in argv:
            path = Path(argv[argv.index("--out")+1])
            raw = json.loads(path.read_bytes())
            raw["users"] = [{"by_mount": None, "files_by_mount": {}, "uid": 1, "name": "a"}]
            path.write_text(json.dumps(raw))
        return result
    host.scan = malformed
    assert host.run() == 0
    assert host.volumes()["/data"]["capture"] == prior
    assert host.volumes()["/data"]["last_attempt"]["outcome"] == "failed"
    assert host.volumes()["/home"]["last_attempt"]["outcome"] == "complete"
