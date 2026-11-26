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
