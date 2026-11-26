# A disposable storage host for run-agent-channel-e2e.sh: Ubuntu 20.04, the
# oldest fleet release (Python 3.8, systemd 245, OpenSSH 8.2, glibc 2.31),
# booted with systemd as PID 1 so units, sudo and systemd-run run for real.
FROM ubuntu:20.04@sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus openssh-server sudo python3 gcc make libc6-dev \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl enable ssh \
 && systemctl mask getty@tty1.service systemd-logind.service \
 && useradd -m -s /bin/bash admin \
 && echo 'admin ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/admin && chmod 0440 /etc/sudoers.d/admin \
 && useradd -m -s /bin/sh monitoring \
 && install -d -m 0700 -o admin -g admin /home/admin/.ssh \
 && install -d -m 0700 -o monitoring -g monitoring /home/monitoring/.ssh
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
