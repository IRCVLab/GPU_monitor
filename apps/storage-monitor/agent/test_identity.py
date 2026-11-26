"""Agent bug repro A2 from docs/redesign/bug-catalog.md.

Strict expected failure until M5 keys slow-capture reuse on volume identity.
"""
import pytest

from agent.test_runner import HOUR, FakeScanner, run, ssd_root_hdd_data, write_config


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="A2: a mount id change alone breaks slow-capture reuse and forces an HDD rescan")
def test_a2_same_id_across_mount_namespaces(tmp_path):
    # Mount ids are local to a mount namespace: the same /dev/sda1 and
    # /dev/sdb1 mounts show different ids to a systemd unit with its own
    # namespace. Device, source, root, fstype and mountpoint are unchanged.
    config = write_config(tmp_path)
    scanner = FakeScanner()
    run(config, ssd_root_hdd_data(root_id=1, data_id=2), scanner, now=5 * HOUR + 30, scheduled=True)

    run(config, ssd_root_hdd_data(root_id=2801, data_id=2802), scanner, now=6 * HOUR + 30, scheduled=True)

    assert scanner.calls[:2] == [("/home",), ("/data",)]
    assert scanner.calls[2:] == [("/home",)], (
        "A2: only the mount ids changed, yet the hourly tick rescanned %r instead of reusing the HDD capture"
        % (scanner.calls[2:],))
