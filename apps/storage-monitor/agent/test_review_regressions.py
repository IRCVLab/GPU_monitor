"""M5b review regressions exercised at group and durable-run boundaries."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from agent.storage_viz_agent import capture, identity
from agent.test_runner import Host, line


@pytest.fixture(scope="module")
def hstscan(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("real scanner needs cc")
    binary = tmp_path_factory.mktemp("bin") / "hstscan"
    subprocess.run(["cc", "-O2", "-pthread", str(Path(__file__).resolve().parents[1]/"scanner/hstscan.c"),
                    "-o", str(binary)], check=True)
    return binary


@pytest.mark.parametrize("case", ["mtime", "username", "mount_bytes", "clock"])
def test_group_keeps_both_volumes(tmp_path, hstscan, case):
    dirs = [tmp_path/"good", tmp_path/os.fsdecode(b"bad-\xff" if case == "mount_bytes" else b"second")]
    volumes = {}
    st_dev = tmp_path.stat().st_dev
    device = "%d:%d" % (os.major(st_dev), os.minor(st_dev))
    out = tmp_path/"scan.json"
    argv = [str(hstscan), "--threads", "1", "--out", str(out)]
    for idx, path in enumerate(dirs):
        path.mkdir()
        (path/"sub").mkdir()
        file = path/"sub/file"
        file.write_bytes(b"x"*8192)
        if idx and case == "mtime":
            os.utime(file, (-315619200, -315619200))
            os.utime(path/"sub", (5000000000, 5000000000))
        vid = "v-%016x" % idx
        volumes[vid] = [identity.Root(str(path), str(path), device, vid, "alias", "b-%016x"%idx,
                                      "data", "ext4", "ssd", "resolved")]
        argv.extend(["--target", str(path), device, "data"])
    subprocess.run(argv, check=True)
    raw = capture.read(out)
    if case == "username":
        raw["users"][0]["name"] = "x"*200
    if case == "mtime":
        raw["stale"] = [dict(row, mtime=5000000000) for row in raw["top_files"]]
    result = capture.split_group_output(raw, volumes, "a"*32, "fast", 100, 90 if case=="clock" else 101)
    assert set(result) == set(volumes)
    second = result["v-%016x"%1]
    root, = second["roots"]
    assert root["top_files"] and root["users"]
    if case == "mtime":
        assert root["top_files"][0]["mtime"] == 0
        assert root["tree"]["children"][0]["mtime"] == 4102444800
        assert root["stale"][0]["mtime"] == 4102444800
    if case == "username":
        assert len(root["users"][0]["name"]) <= 128
    if case == "clock":
        assert second["scan_finished_unix"] == 100


def test_oversized_mount_table_fails_closed_and_next_run_commits(tmp_path):
    host = Host(tmp_path)
    host.run()
    seq = host.manifest()["seq"]
    host.mounts += "".join("\n%d 2 0:%d / /data/docker/%064x/merged rw - overlay overlay rw"
                           % (100+i, 100+i, i) for i in range(900))
    for _ in range(2):
        host.now += 4000
        assert host.run() == 0
        m = host.manifest()
        assert m["mount_table"]["status"] == "unreadable" and not m["mount_table"]["within_roots"]
        assert m["seq"] > seq and len((host.data/"manifest.json").read_bytes()) <= 65536
        assert all(v["capture"] for v in m["volumes"]) and not (host.data/"run.json").exists()
        seq = m["seq"]


def test_socket_request_does_not_block_scanning(tmp_path, monkeypatch, caplog):
    host = Host(tmp_path)
    host.request()
    monkeypatch.chdir(host.data/"requests")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind("b"*32)
        assert host.run() == 0
    assert len(host.calls) == 2 and host.manifest()["requests"][0]["outcome"] == "complete"
    assert "b"*32 in caplog.text


def test_requests_swapped_before_unlink_and_unremovable_file(tmp_path, monkeypatch, caplog):
    host = Host(tmp_path)
    host.request("a"*32)
    host.request("b"*32)
    pending = host.data/"requests"
    original = Path.unlink
    def swap(path, *args, **kwargs):
        if path.parent == pending:
            if path.name.startswith("a") and path.is_file():
                original(path)
                path.mkdir()
            if path.name.startswith("b"):
                raise PermissionError("denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", swap)
    assert host.run() == 0 and len(host.calls) == 2
    assert len(host.manifest()["requests"]) == 2
    assert "Cannot remove recorded request" in caplog.text


def test_pass_identity_survives_recovery_with_clock_rollback(tmp_path, monkeypatch):
    host = Host(tmp_path)
    monkeypatch.setenv("INVOCATION_ID", "a"*32)
    class Crash(Exception):
        pass
    def inject(step):
        if step == "capture" and len(host.calls) == 1:
            host.request()
            host.now -= 500
        if step == "record" and len(host.calls) == 2:
            record = json.loads((host.data/"run.json").read_text())
            if record["current"] and record["request_ids"]:
                raise Crash
    with pytest.raises(Crash):
        host.run(persisted=inject)
    stale = json.loads((host.data/"run.json").read_text())
    assert stale["run_id"] != host.manifest()["last_run"]["run_id"]
    assert stale["started_unix"] == host.now
    recovered = []
    monkeypatch.setenv("INVOCATION_ID", "b"*32)
    host.run(scheduled=True, persisted=lambda step: recovered.append(host.manifest()) if step == "manifest" else None)
    assert recovered[0]["last_run"]["run_id"] == stale["run_id"]
    assert recovered[0]["requests"][0]["outcome"] == "interrupted"
    assert all(v["last_attempt"]["outcome"] == "interrupted" for v in recovered[0]["volumes"])


def test_absent_capture_directories_collected(tmp_path):
    host = Host(tmp_path)
    host.run()
    old = host.volumes()["/data"]["volume_id"]
    orphan = host.data/"captures"/("x-"+"b"*16)
    orphan.mkdir()
    (orphan/("c"*64+".json.gz")).write_bytes(b"prior boot")
    host.mounts = line("8:1","/")
    host.run()
    assert not orphan.exists() and not (host.data/"captures"/old).exists()
    assert len(list((host.data/"captures").iterdir())) == 1


def test_alias_conflict_is_a_volume_diagnostic(tmp_path, monkeypatch):
    from agent import release_cli
    host = Host(tmp_path)
    host.mounts = line("8:1", "/disk-a", subtree="/sub1") + "\n" + line("8:1", "/disk-b", 2, "/sub2")
    host.mounts = host.mounts.replace("ext4", "btrfs")
    cfg = json.loads(host.config.read_text())
    cfg.update(volume_aliases={"/disk-a":"one", "/disk-b":"two"}, scanner_path="/bin/true")
    host.config.write_text(json.dumps(cfg))
    monkeypatch.setattr(release_cli.runner.mount_policy, "read_mountinfo", lambda:host.mounts)
    assert "conflicting volume aliases" in release_cli.check_config(str(host.config))
    assert host.run() == 0
    assert host.manifest()["mount_table"]["status"] == "ok"
    assert all(v["diagnostic"] == "VOLUME_ALIAS_CONFLICT" and v["capture"] for v in host.manifest()["volumes"])


def test_maximum_receipt_batch_survives_budget_fallback(tmp_path):
    from agent.storage_viz_agent import publish, runner
    host = Host(tmp_path)
    for n in range(runner.RECEIPT_WINDOW):
        host.request("%032x" % n)
    host.run()
    manifest = host.manifest()
    expected = manifest["requests"]
    assert len(expected) == 16
    base = manifest["volumes"][0]
    manifest["volumes"] = [dict(base, volume_id="v-%016x"%n, mountpoint="/disk%d"%n,
                                roots=[dict(scan_root="/disk%d"%n, binding="b-%016x"%n, prune_class="data")])
                           for n in range(160)]
    publish.commit_manifest(host.data, manifest, host.now)
    wire = host.manifest()
    assert len((host.data/"manifest.json").read_bytes()) <= 64*1024
    assert wire["mount_table"]["status"] == "unreadable"
    assert wire["requests"] == expected and wire["volumes"]
    assert all(v["capacity"] is None for v in wire["volumes"])


def test_scanner_reports_errno_and_uses_monotonic_progress(tmp_path, hstscan):
    source = Path(__file__).resolve().parents[1]/"scanner/hstscan.c"
    harness = tmp_path/"check.c"
    harness.write_text('''
#include <assert.h>
#define main scanner_main
#include "hstscan.c"
#undef main
int __real_pthread_condattr_setclock(pthread_condattr_t *, clockid_t);
static int monotonic_cond;
int __wrap_pthread_condattr_setclock(pthread_condattr_t *a, clockid_t clock) {
    assert(clock == CLOCK_MONOTONIC);
    monotonic_cond++;
    return __real_pthread_condattr_setclock(a, clock);
}
int main(int argc, char **argv) {
    assert(!strcmp(errno_reason(EIO), "EIO"));
    assert(!strcmp(errno_reason(EMFILE), "EMFILE"));
    assert(!strcmp(errno_reason(ENOMEM), "ENOMEM"));
    int rc = scanner_main(argc, argv);
    assert(monotonic_cond == 1);
    return rc;
}
'''.replace('#include <assert.h>\n', '#define _GNU_SOURCE\n#include <assert.h>\n'))
    binary = tmp_path/"check"
    subprocess.run(["cc", "-pthread", "-I", str(source.parent), str(harness),
                    "-Wl,--wrap=pthread_condattr_setclock", "-o", str(binary)], check=True)
    subprocess.run([str(binary), "--progress", str(tmp_path/"progress"), "--out", str(tmp_path/"out"),
                    str(tmp_path)], check=True)
    for errno, code in (("EIO","EACCES"),("EMFILE","FD_BUDGET_REOPEN_FAILED"),("ENOMEM","FD_BUDGET_REOPEN_FAILED")):
        assert capture.file_row(dict(path="/data/a", reason=errno), "errors")["reason"] == code


def test_duplicate_arriving_at_unlock_cannot_spin(tmp_path):
    host = Host(tmp_path)
    host.request()
    clears = []
    def replace_duplicate(step):
        if step == "clear":
            clears.append(1)
            assert len(clears) == 1
            host.request()
    assert host.run(persisted=replace_duplicate) == 0
    assert len(host.manifest()["requests"]) == 1


def test_pass_identity_changes_after_reacquiring_lock(tmp_path, monkeypatch):
    host = Host(tmp_path)
    monkeypatch.setenv("INVOCATION_ID", "1"*32)
    passes = []
    def enqueue_at_unlock(step):
        if step == "clear":
            passes.append(host.manifest()["last_run"])
            if len(passes) == 1:
                host.request()
                host.now -= 5
    assert host.run(persisted=enqueue_at_unlock) == 0
    first, second = passes
    assert first["run_id"] == "1"*32 and second["run_id"] != first["run_id"]
    assert second["started_unix"] < first["started_unix"]
