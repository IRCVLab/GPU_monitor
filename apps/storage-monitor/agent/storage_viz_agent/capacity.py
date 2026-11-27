import os


def sample(root, previous, now):
    try:
        fd = os.open(root.mountpoint, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_RDONLY)
        try:
            st_dev = os.fstat(fd).st_dev
            if root.device != "%d:%d" % (os.major(st_dev), os.minor(st_dev)):
                raise OSError("volume changed")
            stats = os.fstatvfs(fd)
        finally:
            os.close(fd)
    except OSError:
        return dict(previous or dict(total=0, used=0, avail=0, observed_unix=0), status="failed")
    block = stats.f_frsize or stats.f_bsize
    return dict(total=stats.f_blocks * block, used=(stats.f_blocks - stats.f_bfree) * block,
                avail=stats.f_bavail * block, observed_unix=now, status="ok")


def sample_volume(roots, previous, now):
    """Capacity of one volume. Roots of one filesystem share it, so the first root's sample is
    the volume's. A ZFS pool volume (roots with volume_mountpoint) is the sum of its datasets'
    used bytes plus the pool's free space, which every dataset reports, counted once."""
    roots = list(roots)
    if len(roots) < 2 or not all(getattr(r, "volume_mountpoint", None) for r in roots):
        return sample(roots[0], previous, now)
    parts = [sample(r, None, now) for r in roots]
    if any(p["status"] != "ok" for p in parts):
        return dict(previous or dict(total=0, used=0, avail=0, observed_unix=0), status="failed")
    used = sum(p["used"] for p in parts)
    avail = max(p["avail"] for p in parts)
    return dict(total=used + avail, used=used, avail=avail, observed_unix=now, status="ok")
