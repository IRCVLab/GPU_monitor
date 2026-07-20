import unittest

from agent.mount_policy import (
    classify_mount,
    parse_mountinfo,
    read_mountinfo,
    select_scan_roots,
)


def mi(mid, parent, dev, root, mountpoint, options, fstype, source, super_options="rw", optional=""):
    opt = f" {optional}" if optional else ""
    return f"{mid} {parent} {dev} {root} {mountpoint} {options}{opt} - {fstype} {source} {super_options}"


class MountPolicyTests(unittest.TestCase):
    def test_root_scans_home_only(self):
        entries = parse_mountinfo(mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"))

        result = select_scan_roots(entries)

        self.assertEqual([record.mountpoint for record in result.selected], ["/home"])
        self.assertEqual(result.selected[0].source_mountpoint, "/")
        self.assertEqual(result.skipped[0].reason, "root-limited-to-home")

    def test_separate_home_is_scanned_once(self):
        text = "\n".join([
            mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
            mi(2, 1, "8:2", "/", "/home", "rw", "xfs", "/dev/sda2"),
        ])
        entries = parse_mountinfo(text)

        result = select_scan_roots(entries)

        self.assertEqual([record.mountpoint for record in result.selected], ["/home"])
        self.assertEqual(result.selected[0].source_mount_id, 2)
        self.assertIn((1, "root-home-covered"), [(s.mount_id, s.reason) for s in result.skipped])

    def test_prohibited_or_unsupported_home_mount_blocks_root_fallback(self):
        cases = [
            (mi(2, 1, "0:42", "/", "/home", "rw", "nfs", "server:/home"), "remote-fs"),
            (mi(3, 1, "0:43", "/", "/home", "rw", "fuse", "portal"), "generic-fuse"),
            (mi(4, 1, "0:44", "/", "/home", "rw", "weirdfs", "mystery"), "unsupported-fstype"),
        ]
        for home_line, expected_reason in cases:
            with self.subTest(home_line=home_line):
                text = "\n".join([
                    mi(1, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
                    home_line,
                ])

                result = select_scan_roots(parse_mountinfo(text))

                self.assertEqual(result.selected, [])
                self.assertIn(("/home", expected_reason), [(s.mountpoint, s.reason) for s in result.skipped])
                self.assertIn(("/", "root-home-covered"), [(s.mountpoint, s.reason) for s in result.skipped])

    def test_plain_ext4_xfs_lvm_and_mdraid_mounts_are_local(self):
        text = "\n".join([
            mi(10, 1, "8:17", "/", "/srv/ext", "rw", "ext4", "/dev/sdb1"),
            mi(11, 1, "8:18", "/", "/srv/xfs", "rw", "xfs", "/dev/sdc1"),
            mi(12, 1, "253:0", "/", "/srv/lvm", "rw", "ext4", "/dev/mapper/vg-data"),
            mi(13, 1, "9:0", "/", "/srv/md", "rw", "xfs", "/dev/md0"),
        ])

        selected = select_scan_roots(parse_mountinfo(text)).selected

        self.assertEqual([r.mountpoint for r in selected], ["/srv/ext", "/srv/xfs", "/srv/lvm", "/srv/md"])
        self.assertTrue(all(classify_mount(r.entry).status == "selected" for r in selected))

    def test_btrfs_subvolumes_with_distinct_roots_remain_distinct(self):
        text = "\n".join([
            mi(20, 1, "0:45", "/@data", "/data", "rw", "btrfs", "/dev/sda3"),
            mi(21, 20, "0:45", "/@archive", "/archive", "rw", "btrfs", "/dev/sda3"),
        ])

        selected = select_scan_roots(parse_mountinfo(text)).selected

        self.assertEqual([(r.mountpoint, r.entry.root) for r in selected], [("/data", "/@data"), ("/archive", "/@archive")])

    def test_non_subvolume_local_bind_subtrees_are_not_scanned_as_storage_roots(self):
        text = "\n".join([
            mi(22, 1, "8:1", "/var/lib/storage-viz", "/var/lib/storage-viz", "rw", "ext4", "/dev/sda1"),
            mi(23, 1, "8:1", "/tmp/systemd-private/service/tmp", "/tmp", "rw", "ext4", "/dev/sda1"),
        ])

        result = select_scan_roots(parse_mountinfo(text))

        self.assertEqual(result.selected, [])
        self.assertEqual([(row.mountpoint, row.reason) for row in result.skipped], [
            ("/var/lib/storage-viz", "bind-subtree"),
            ("/tmp", "bind-subtree"),
        ])

    def test_zfs_datasets_with_distinct_sources_remain_distinct(self):
        text = "\n".join([
            mi(30, 1, "0:50", "/", "/tank", "rw", "zfs", "tank"),
            mi(31, 1, "0:51", "/", "/tank/home", "rw", "zfs", "tank/home"),
        ])

        selected = select_scan_roots(parse_mountinfo(text)).selected

        self.assertEqual([(r.mountpoint, r.entry.source) for r in selected], [("/tank", "tank"), ("/tank/home", "tank/home")])

    def test_complete_denylist_is_rejected(self):
        cases = {
            "nfs": "remote-fs", "nfs4": "remote-fs", "cifs": "remote-fs", "smb3": "remote-fs",
            "sshfs": "remote-fs", "fuse.sshfs": "remote-fs", "ceph": "remote-fs", "fuse.ceph": "remote-fs",
            "fuse.rclone": "remote-fs", "fuse.davfs": "remote-fs", "glusterfs": "remote-fs",
            "lustre": "remote-fs", "gpfs": "remote-fs", "9p": "remote-fs",
            "proc": "virtual-fs", "sysfs": "virtual-fs", "tmpfs": "virtual-fs", "devtmpfs": "virtual-fs",
            "devpts": "virtual-fs", "cgroup": "virtual-fs", "cgroup2": "virtual-fs", "pstore": "virtual-fs",
            "securityfs": "virtual-fs", "debugfs": "virtual-fs", "tracefs": "virtual-fs", "configfs": "virtual-fs",
            "fusectl": "virtual-fs", "mqueue": "virtual-fs", "hugetlbfs": "virtual-fs", "ramfs": "virtual-fs",
            "autofs": "virtual-fs", "binfmt_misc": "virtual-fs", "nsfs": "virtual-fs", "bpf": "virtual-fs",
            "rpc_pipefs": "virtual-fs",
            "overlay": "container-fs", "aufs": "container-fs", "squashfs": "container-fs",
        }
        for fstype, reason in cases.items():
            with self.subTest(fstype=fstype):
                entry = parse_mountinfo(mi(40, 1, "0:1", "/", f"/mnt/{fstype}", "rw", fstype, "server:/export"))[0]
                decision = classify_mount(entry)
                self.assertEqual(decision.status, "prohibited")
                self.assertEqual(decision.reason, reason)

    def test_netdev_remote_colon_and_unc_sources_are_rejected(self):
        text = "\n".join([
            mi(50, 1, "8:1", "/", "/netdev", "rw,_netdev", "ext4", "/dev/sda1"),
            mi(51, 1, "8:2", "/", "/colon", "rw", "ext4", "host:/path"),
            mi(52, 1, "8:3", "/", "/unc", "rw", "ext4", "//host/share"),
        ])
        decisions = [classify_mount(e) for e in parse_mountinfo(text)]
        self.assertEqual([(d.status, d.reason) for d in decisions], [
            ("prohibited", "netdev"),
            ("prohibited", "remote-source"),
            ("prohibited", "remote-source"),
        ])

    def test_generic_fuse_virtual_overlay_squashfs_aufs_and_loop_sources_are_rejected(self):
        text = "\n".join([
            mi(60, 1, "0:60", "/", "/fuse", "rw", "fuse", "portal"),
            mi(61, 1, "0:61", "/", "/overlay", "rw", "overlay", "overlay"),
            mi(62, 1, "7:0", "/", "/loopdev", "rw", "ext4", "/dev/loop0"),
            mi(63, 1, "0:63", "/", "/loopfile", "rw", "ext4", "/images/disk.img"),
            mi(64, 1, "0:64", "/", "/squash", "rw", "squashfs", "/dev/loop1"),
            mi(65, 1, "0:65", "/", "/aufs", "rw", "aufs", "none"),
        ])
        self.assertEqual([(classify_mount(e).status, classify_mount(e).reason) for e in parse_mountinfo(text)], [
            ("prohibited", "generic-fuse"),
            ("prohibited", "container-fs"),
            ("prohibited", "loop-source"),
            ("prohibited", "loop-source"),
            ("prohibited", "container-fs"),
            ("prohibited", "container-fs"),
        ])

    def test_same_identity_alias_cannot_replace_root_home_policy(self):
        text = "\n".join([
            mi(20, 0, "8:1", "/", "/", "rw", "ext4", "/dev/sda1"),
            mi(10, 20, "8:1", "/", "/mnt/root-bind", "rw", "ext4", "/dev/sda1"),
        ])

        result = select_scan_roots(parse_mountinfo(text))

        self.assertEqual([(r.entry.mount_id, r.mountpoint, r.reason) for r in result.selected], [(20, "/home", "root-home")])
        self.assertIn((10, "/mnt/root-bind", 20, "duplicate"), [(s.mount_id, s.mountpoint, s.chosen_mount_id, s.reason) for s in result.skipped])

    def test_duplicate_identity_uses_lowest_mount_id_then_shortest_then_lexical(self):
        text = "\n".join([
            mi(80, 1, "8:1", "/", "/z-long", "rw", "ext4", "/dev/sda1"),
            mi(80, 1, "8:1", "/", "/a", "rw", "ext4", "/dev/sda1"),
            mi(79, 1, "8:1", "/", "/zz", "rw", "ext4", "/dev/sda1"),
            mi(79, 1, "8:1", "/", "/aa", "rw", "ext4", "/dev/sda1"),
        ])

        result = select_scan_roots(parse_mountinfo(text))

        self.assertEqual([(r.entry.mount_id, r.mountpoint) for r in result.selected], [(79, "/aa")])
        self.assertEqual([(s.mount_id, s.mountpoint, s.chosen_mount_id, s.reason) for s in result.skipped], [
            (79, "/zz", 79, "duplicate"),
            (80, "/a", 79, "duplicate"),
            (80, "/z-long", 79, "duplicate"),
        ])

    def test_nested_mounts_are_separate_roots(self):
        text = "\n".join([
            mi(90, 1, "8:1", "/", "/srv", "rw", "ext4", "/dev/sdb1"),
            mi(91, 90, "8:2", "/", "/srv/projects", "rw", "xfs", "/dev/sdc1"),
        ])

        self.assertEqual([r.mountpoint for r in select_scan_roots(parse_mountinfo(text)).selected], ["/srv", "/srv/projects"])

    def test_unsupported_mount_is_reported_not_guessed(self):
        entry = parse_mountinfo(mi(100, 1, "0:100", "/", "/mystery", "rw", "weirdfs", "mystery"))[0]

        decision = classify_mount(entry)
        result = select_scan_roots([entry])

        self.assertEqual((decision.status, decision.reason), ("unsupported", "unsupported-fstype"))
        self.assertEqual([(s.mount_id, s.reason) for s in result.skipped], [(100, "unsupported-fstype")])
        self.assertEqual(result.selected, [])

    def test_malformed_mountinfo_lines_and_escaped_mountpoints(self):
        text = "\n".join([
            mi(110, 1, "8:1", "/", "/mnt/space\\040tab\\011slash\\134", "rw", "ext4", "/dev/sda1"),
            "this is not mountinfo",
            "111 1 8:2 / /bad rw ext4 /dev/sdb1 rw",
        ])

        with self.assertRaises(ValueError) as cm:
            parse_mountinfo(text)
        self.assertIn("line 2", str(cm.exception))

        parsed = parse_mountinfo(text.splitlines()[0])
        self.assertEqual(parsed[0].mountpoint, "/mnt/space tab\tslash\\")
        self.assertEqual(parsed[0].root, "/")

    def test_file_read_adapter_isolated(self):
        self.assertTrue(callable(read_mountinfo))


if __name__ == "__main__":
    unittest.main()
