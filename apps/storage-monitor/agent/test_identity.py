from pathlib import Path
import shutil
import subprocess

from agent.storage_viz_agent import identity, mount_policy, plan
from agent.test_runner import Host, Resolver, line


def selected(text):
    return mount_policy.select_scan_roots(mount_policy.parse_mountinfo(text)).selected


def test_a2_same_id_across_mount_namespaces(tmp_path):
    # Compare the actual namespace-local IDs when user namespaces are enabled.
    outside, inside = 1, 2801
    if shutil.which("unshare"):
        ns = subprocess.run(["unshare", "-rm", "cat", "/proc/self/mountinfo"], capture_output=True)
        if ns.returncode == 0:
            inside = int(ns.stdout.split()[0])
            outside = int(Path("/proc/self/mountinfo").read_bytes().split()[0])
    host = Host(tmp_path)
    host.mounts = line("8:2", "/data", outside)
    host.run(scheduled=True)
    old = host.volumes()
    host.mounts = line("8:2", "/data", inside)
    host.now += 3600
    host.run(scheduled=True)
    assert len(host.calls) == 1
    assert host.volumes()["/data"]["capture"] == old["/data"]["capture"]


def test_a2_changed_bind_root_forces_rescan(tmp_path):
    host = Host(tmp_path)
    host.mounts = line("8:2", "/data", subtree="/a").replace("ext4", "btrfs")
    host.run(scheduled=True)
    old = host.volumes()["/data"]
    host.mounts = host.mounts.replace(" /a ", " /b ")
    host.run(scheduled=True)
    new = host.volumes()["/data"]
    assert new["volume_id"] == old["volume_id"] and new["roots"] != old["roots"]
    assert len(host.calls) == 2


def test_a2_duplicate_uuid_is_ambiguous(tmp_path):
    sysfs, devfs, udev = (tmp_path / n for n in ("sys","dev","udev"))
    udev.mkdir()
    links = devfs / "disk/by-uuid"
    links.mkdir(parents=True)
    (sysfs / "dev/block").mkdir(parents=True)
    text = line("8:1", "/a") + "\n" + line("8:2", "/b", 2)
    for device, name in (("8:1","sda"), ("8:2","sdb")):
        node = sysfs / "block" / name
        node.mkdir(parents=True)
        (sysfs/"dev/block"/device).symlink_to(node)
        (devfs/name).touch()
    (links/"shared").symlink_to(devfs/"sda")
    def resolve(aliases):
        return identity.resolve(selected(text), aliases, "config", Resolver(), sysfs, devfs, udev, "boot")
    a, b = resolve({})
    assert a.identity == "uuid" and b.identity == "ambiguous"
    (udev/"b8:2").write_text("E:ID_FS_UUID=shared\n")
    a, b = resolve({})
    assert a.identity == b.identity == "ambiguous" and a.volume_id != b.volume_id
    assert len(plan.validate_partition([a,b])) == 2
    assert resolve({"/a":"unique"})[0].identity == "alias"
