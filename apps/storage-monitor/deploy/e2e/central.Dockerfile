# The rehearsal's central host for run-dashboard-upgrade-rehearsal.sh: Ubuntu
# 22.04 like the real central host (Python 3.10, systemd 249), booted with systemd
# as PID 1 so the puller, dashboard and proxy units run with their real hardening.
FROM ubuntu:22.04@sha256:b000e69a50ff940390233025e7a337fb8469b509e2334fa4d018ac30f3e18cdb
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus python3 git openssh-client openssl ca-certificates iproute2 \
 && rm -rf /var/lib/apt/lists/* \
 && systemctl mask getty@tty1.service systemd-logind.service
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
