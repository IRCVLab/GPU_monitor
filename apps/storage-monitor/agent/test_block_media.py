from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.block_media import BlockMediaResolver, MediaResult, capacity_id


class FakeSysfs:
    def __init__(self, root: Path):
        self.root = root
        (root / "dev" / "block").mkdir(parents=True)
        (root / "block").mkdir()
        (root / "class" / "block").mkdir(parents=True)

    def disk(self, name: str, rotational: str | None = None) -> Path:
        disk = self.root / "block" / name
        disk.mkdir(parents=True, exist_ok=True)
        if rotational is not None:
            (disk / "queue").mkdir(exist_ok=True)
            (disk / "queue" / "rotational").write_text(rotational, encoding="utf-8")
        return disk

    def partition(self, disk_name: str, part_name: str, major_minor: str) -> Path:
        disk = self.disk(disk_name)
        part = disk / part_name
        part.mkdir(exist_ok=True)
        self.devlink(major_minor, Path("../../block") / disk_name / part_name)
        return part

    def devlink(self, major_minor: str, target: Path | str) -> None:
        link = self.root / "dev" / "block" / major_minor
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    def slave(self, holder: str, slave: str) -> None:
        holder_path = self.root / "block" / holder
        slave_path = self.root / "block" / slave
        holder_path.mkdir(parents=True, exist_ok=True)
        slave_path.mkdir(parents=True, exist_ok=True)
        slaves = holder_path / "slaves"
        slaves.mkdir(exist_ok=True)
        (slaves / slave).symlink_to(Path("../../") / slave)

    def real_linux_disk(self, name: str, major_minor: str, rotational: str) -> Path:
        target = self.root / "devices" / "pci0000:00" / "0000:00:17.0" / "block" / name
        target.mkdir(parents=True)
        (target / "queue").mkdir()
        (target / "queue" / "rotational").write_text(rotational, encoding="utf-8")
        (self.root / "class" / "block" / name).symlink_to(Path("../../devices/pci0000:00/0000:00:17.0/block") / name)
        (self.root / "block" / name).symlink_to(Path("../devices/pci0000:00/0000:00:17.0/block") / name)
        self.devlink(major_minor, Path("../../devices/pci0000:00/0000:00:17.0/block") / name)
        return target


class CapacityIdTests(unittest.TestCase):
    def test_capacity_id_canonicalizes_valid_major_minor(self):
        self.assertEqual(capacity_id("8:1"), "dev-8-1")
        self.assertEqual(capacity_id("253:0"), "dev-253-0")
        self.assertEqual(capacity_id("0008:0001"), "dev-8-1")

    def test_capacity_id_rejects_invalid_or_unbounded_major_minor(self):
        invalid = ["", "8", "8:1:2", "a:1", "1:a", "-1:1", "0:0", "0:1", "12345678901:1", "1:12345678901"]
        self.assertEqual([capacity_id(value) for value in invalid], [None] * len(invalid))


class BlockMediaResolverTests(unittest.TestCase):
    def with_sysfs(self):
        return tempfile.TemporaryDirectory()

    def result(self, sysfs: FakeSysfs, major_minor: str, **kwargs) -> MediaResult:
        return BlockMediaResolver(sysfs.root, **kwargs).resolve(major_minor)

    def test_resolves_real_linux_devices_topology_via_class_block_membership(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.real_linux_disk("sda", "8:0", "1\n")
            fs.real_linux_disk("nvme0n1", "259:0", "0\n")

            self.assertEqual(self.result(fs, "8:0"), MediaResult("dev-8-0", "hdd", "resolved"))
            self.assertEqual(self.result(fs, "259:0"), MediaResult("dev-259-0", "ssd", "resolved"))

    def test_resolves_real_linux_partition_by_ascending_to_parent(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            disk = fs.real_linux_disk("sda", "8:0", "1\n")
            part = disk / "sda1"
            part.mkdir()
            fs.devlink("8:1", Path("../../devices/pci0000:00/0000:00:17.0/block/sda/sda1"))

            self.assertEqual(self.result(fs, "8:1"), MediaResult("dev-8-1", "hdd", "resolved"))

    def test_resolves_ssd_and_hdd_from_whole_disk_rotational(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("nvme0n1", "0\n")
            fs.disk("sda", "1\n")
            fs.devlink("259:0", "../../block/nvme0n1")
            fs.devlink("8:0", "../../block/sda")

            self.assertEqual(self.result(fs, "259:0"), MediaResult("dev-259-0", "ssd", "resolved"))
            self.assertEqual(self.result(fs, "8:0"), MediaResult("dev-8-0", "hdd", "resolved"))

    def test_partition_ascends_to_parent_whole_disk(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("sda", "1\n")
            fs.partition("sda", "sda1", "8:1")

            self.assertEqual(self.result(fs, "8:1"), MediaResult("dev-8-1", "hdd", "resolved"))

    def test_dm_and_md_slaves_resolve_through_leaf_devices(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("dm-0")
            fs.disk("sdb", "0\n")
            fs.disk("sdc", "0\n")
            fs.devlink("253:0", "../../block/dm-0")
            fs.slave("dm-0", "sdb")
            fs.slave("dm-0", "sdc")

            self.assertEqual(self.result(fs, "253:0"), MediaResult("dev-253-0", "ssd", "resolved"))

            fs.disk("md0")
            fs.disk("sdd", "1\n")
            fs.disk("sde", "1\n")
            fs.devlink("9:0", "../../block/md0")
            fs.slave("md0", "sdd")
            fs.slave("md0", "sde")
            self.assertEqual(self.result(fs, "9:0"), MediaResult("dev-9-0", "hdd", "resolved"))

    def test_mixed_slave_media_is_resolved_as_mixed(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("dm-0")
            fs.disk("sdb", "0\n")
            fs.disk("sdc", "1\n")
            fs.devlink("253:0", "../../block/dm-0")
            fs.slave("dm-0", "sdb")
            fs.slave("dm-0", "sdc")

            self.assertEqual(self.result(fs, "253:0"), MediaResult("dev-253-0", "mixed", "resolved"))

    def test_holder_with_slaves_uses_leaf_media_not_holder_rotational(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("dm-0", "0\n")
            fs.disk("sdb", "0\n")
            fs.disk("sdc", "1\n")
            fs.devlink("253:0", "../../block/dm-0")
            fs.slave("dm-0", "sdb")
            fs.slave("dm-0", "sdc")

            self.assertEqual(self.result(fs, "253:0"), MediaResult("dev-253-0", "mixed", "resolved"))

    def test_runtime_error_from_dev_block_symlink_loop_returns_unknown(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            (fs.root / "dev" / "block" / "8:0").symlink_to("8:0")

            self.assertEqual(self.result(fs, "8:0"), MediaResult("dev-8-0", "unknown", "unresolved"))

    def test_runtime_error_from_slave_symlink_loop_returns_unknown(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("dm-0")
            fs.devlink("253:0", "../../block/dm-0")
            slaves = fs.root / "block" / "dm-0" / "slaves"
            slaves.mkdir()
            (slaves / "loop").symlink_to("loop")

            self.assertEqual(self.result(fs, "253:0"), MediaResult("dev-253-0", "unknown", "unresolved"))

    def test_missing_unreadable_cycle_and_no_leaf_are_unknown_unresolved(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("missing")
            fs.devlink("8:16", "../../block/missing")
            self.assertEqual(self.result(fs, "8:16"), MediaResult("dev-8-16", "unknown", "unresolved"))

            fs.disk("bad")
            (fs.root / "block" / "bad" / "queue").mkdir()
            (fs.root / "block" / "bad" / "queue" / "rotational").mkdir()
            fs.devlink("8:32", "../../block/bad")
            self.assertEqual(self.result(fs, "8:32"), MediaResult("dev-8-32", "unknown", "unresolved"))

            fs.disk("loopa")
            fs.disk("loopb")
            fs.devlink("8:48", "../../block/loopa")
            fs.slave("loopa", "loopb")
            fs.slave("loopb", "loopa")
            self.assertEqual(self.result(fs, "8:48"), MediaResult("dev-8-48", "unknown", "unresolved"))

    def test_depth_node_and_containment_bounds_return_unknown(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            for idx in range(4):
                fs.disk(f"dm-{idx}")
            fs.disk("sda", "0\n")
            fs.devlink("253:0", "../../block/dm-0")
            for idx in range(3):
                fs.slave(f"dm-{idx}", f"dm-{idx + 1}")
            fs.slave("dm-3", "sda")
            self.assertEqual(self.result(fs, "253:0", max_depth=2), MediaResult("dev-253-0", "unknown", "unresolved"))
            self.assertEqual(self.result(fs, "253:0", max_nodes=3), MediaResult("dev-253-0", "unknown", "unresolved"))

            outside = Path(td) / "outside"
            outside.mkdir()
            fs.devlink("8:64", "../../outside")
            self.assertEqual(self.result(fs, "8:64"), MediaResult("dev-8-64", "unknown", "unresolved"))

            outside_disk = outside / "fakeblock"
            outside_disk.mkdir()
            (outside_disk / "queue").mkdir()
            (outside_disk / "queue" / "rotational").write_text("0\n", encoding="utf-8")
            (fs.root / "class" / "block" / "fakeblock").symlink_to(Path("../../outside/fakeblock"))
            fs.devlink("8:65", "../../outside/fakeblock")
            self.assertEqual(self.result(fs, "8:65"), MediaResult("dev-8-65", "unknown", "unresolved"))

    def test_results_are_cached_by_canonical_major_minor(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("sda", "0\n")
            fs.devlink("8:0", "../../block/sda")
            resolver = BlockMediaResolver(fs.root)

            with mock.patch.object(resolver, "_resolve_uncached", wraps=resolver._resolve_uncached) as uncached:
                self.assertEqual(resolver.resolve("0008:0000"), MediaResult("dev-8-0", "ssd", "resolved"))
                (fs.root / "block" / "sda" / "queue" / "rotational").write_text("1\n", encoding="utf-8")
                self.assertEqual(resolver.resolve("8:0"), MediaResult("dev-8-0", "ssd", "resolved"))
                self.assertEqual(uncached.call_count, 1)
                self.assertEqual(uncached.call_args.args[0], "8:0")

    def test_invalid_input_unknown_and_no_subprocess_use(self):
        with self.with_sysfs() as td:
            fs = FakeSysfs(Path(td))
            fs.disk("sda", "0\n")
            fs.devlink("8:0", "../../block/sda")
            resolver = BlockMediaResolver(fs.root)
            with mock.patch.object(subprocess, "run", side_effect=AssertionError("subprocess.run used")), \
                 mock.patch.object(subprocess, "Popen", side_effect=AssertionError("subprocess.Popen used")):
                self.assertEqual(resolver.resolve("8:0"), MediaResult("dev-8-0", "ssd", "resolved"))
                self.assertEqual(resolver.resolve("bad"), MediaResult(None, "unknown", "unresolved"))


if __name__ == "__main__":
    unittest.main()
