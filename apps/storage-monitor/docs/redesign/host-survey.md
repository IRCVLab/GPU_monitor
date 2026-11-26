# Storage host survey (2026-11-26)

These facts were collected read-only over SSH from the seven scanned storage
hosts. The survey script is `host_survey.py` in the session scratchpad. It uses
the dev GPU monitor's stored credentials, decrypted in memory only, and runs a
fixed list of read-only commands. Raw output stayed local. This file holds no
UUID values and no credentials.

| Host | OS | Kernel | glibc | Python | OpenSSH | systemd | Local filesystems | Disks | HDD scheduler | Agent installed |
|---|---|---|---|---|---|---|---|---|---|---|
| Poseidon | Ubuntu 20.04.5 | 5.15.0-87 | 2.31 | 3.8.10 | 8.2p1 | 245 | ext4 x6 | 3 HDD, 3 SSD | mq-deadline | yes |
| Hinton | Ubuntu 22.04.4 | 5.15.0-91 | 2.35 | 3.10.12 | 8.9p1 | 249 | ext4 x2, xfs x2 | 2 HDD, 3 SSD | mq-deadline | yes |
| Turing | Ubuntu 22.04.5 | 5.15.0-122 | 2.35 | 3.10.12 | 8.9p1 | 249 | ext4 x2, xfs x2 | 2 HDD, 3 SSD | mq-deadline | yes |
| Lecun | Ubuntu 22.04.1 | 5.15.0-118 | 2.35 | 3.10.12 | 8.9p1 | 249 | ext4 x2, xfs x1 | 1 HDD, 3 SSD | mq-deadline | yes |
| Ace | Ubuntu 22.04.5 | 6.8.0-83 | 2.35 | 3.10.12 | 8.9p1 | 249 | ext4 x1, xfs x1 | 1 HDD, 2 SSD | mq-deadline | yes |
| Neo | Ubuntu 22.04.5 | 5.15.0-198 | 2.35 | 3.10.12 | 8.9p1 | 249 | ext4 x3 | 2 HDD, 2 SSD | mq-deadline | yes |
| Shannon | Ubuntu 22.04.3 | 5.15.0-179 | 2.35 | 3.10.12 | 8.9p1 | 249 | ext4 x1 | 1 HDD, 1 SSD | mq-deadline | yes |

The hosts also mount NFS and CIFS shares. Mount policy already excludes them
from scanning. All hosts are x86_64.

## Consequences for the target design

- **Python 3.8 floor.** Poseidon runs Python 3.8.10, so agent and gate code
  must run on 3.8. That rules out `match`, PEP 604 unions at runtime
  (`X | Y` outside annotations with `from __future__ import annotations`),
  `str.removeprefix` and other 3.9+ APIs. CI runs the agent suites on 3.8.
- **systemd 245 floor.** `CPUWeight=idle` needs systemd 252, so units use
  `CPUWeight=1` and `IOWeight=1`. The `systemd-run --pipe` self-test sandbox
  and `RestrictAddressFamilies` both work on 245.
- **glibc 2.31 floor.** Build hstscan in an Ubuntu 20.04 container, or link it
  statically. A dynamic build against 2.31 also runs on 2.35.
- **`ssh-keygen -Y verify` is available everywhere.** It needs OpenSSH 8.1 or
  later, and the oldest host has 8.2.
- **Volume identity is simple here.** Every local filesystem is ext4 or xfs
  with a UUID. No duplicate or missing UUIDs were seen, and no ZFS or btrfs is
  present. The ambiguity rule and local aliases stay in the design for future
  hosts, but no host needs an alias today.
- **I/O priority takes effect.** Every HDD uses mq-deadline on kernel 5.15 or
  later. mq-deadline honours I/O priority classes from 5.14 on, so
  `IOSchedulingClass=idle` really yields to training I/O (inferred from the
  kernel changelog, not measured).
- **Bind mounts are snapd namespaces only.** They sit under `/run/snapd/ns`
  and `/run/netns`, outside every scan root. The removal mount guard keeps
  covering the general case.
- **Not surveyed.** H200-1 rejected the stored credentials, and Neumann was
  unreachable. Neither is a storage monitor host.
