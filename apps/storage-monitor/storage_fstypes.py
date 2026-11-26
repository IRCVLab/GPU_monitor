"""Remote filesystem types excluded from local storage accounting."""

REMOTE_FSTYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "sshfs",
        "fuse.sshfs",
        "ceph",
        "fuse.ceph",
        "fuse.rclone",
        "fuse.davfs",
        "glusterfs",
        "lustre",
        "gpfs",
        "9p",
    }
)
