/*
 * hstscan - fast multithreaded directory-size scanner for storage-viz.
 *
 * C11 / pthreads / libc only. No external dependencies.
 *
 * Design summary:
 *   - Mounts discovered by parsing /proc/self/mountinfo; only "local" fstypes
 *     are kept, network/pseudo filesystems are skipped.
 *   - A shared work queue of directories is consumed by a fixed thread pool.
 *     Termination is detected via a pending-items counter guarded by the queue
 *     mutex: when the queue is empty and no worker is busy, all workers wake
 *     and exit. Each scan target runs as its own pass so per-mount stats and
 *     per-target st_dev boundaries stay isolated.
 *   - Directory entries are read with raw getdents64 into a 256KB buffer.
 *   - Hardlink dedup uses a per-thread (dev,ino) hash set for nlink>1 files,
 *     merged into a global set under a mutex; each inode's blocks count once.
 *   - The directory tree is built incrementally (each node protected by its
 *     own lock), then pruned bottom-up before JSON emission so memory stays
 *     bounded. Pruned children fold their bytes into the parent's other_bytes.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#include <time.h>
#include <pwd.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <pthread.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <sys/resource.h>
#if defined(__linux__)
#include <sys/sysmacros.h>
#endif

#define SCANNER_VERSION "0.1.0"
#define SCHEMA_VERSION  1

#define GETDENTS_BUFSZ  (256 * 1024)
#define MAX_BLOCKED     200
#define MAX_STALE       4000
#define DEFAULT_TOP     200
#define DEFAULT_STALE_DAYS 180
#define DEFAULT_PRUNE_HOME_MB 50
#define DEFAULT_PRUNE_DATA_MB 100

/* Linux dirent64 (not exposed by glibc headers). */
struct linux_dirent64 {
    uint64_t d_ino;
    int64_t  d_off;
    uint16_t d_reclen;
    uint8_t  d_type;
    char     d_name[];
};

/* ----------------------------------------------------------------------- *
 *  Small utilities
 * ----------------------------------------------------------------------- */

static void *xmalloc(size_t n) {
    void *p = malloc(n);
    if (!p) { fprintf(stderr, "hstscan: out of memory\n"); exit(2); }
    return p;
}
static void *xcalloc(size_t n, size_t s) {
    void *p = calloc(n, s);
    if (!p) { fprintf(stderr, "hstscan: out of memory\n"); exit(2); }
    return p;
}
static void *xrealloc(void *old, size_t n) {
    void *p = realloc(old, n);
    if (!p) { fprintf(stderr, "hstscan: out of memory\n"); exit(2); }
    return p;
}
static char *xstrdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *p = xmalloc(n);
    memcpy(p, s, n);
    return p;
}

static double now_monotonic(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

/* Join dir + "/" + name into a freshly allocated string (handles trailing /). */
static char *path_join(const char *dir, const char *name) {
    size_t dl = strlen(dir);
    size_t nl = strlen(name);
    bool need_sep = (dl > 0 && dir[dl - 1] != '/');
    char *p = xmalloc(dl + (need_sep ? 1 : 0) + nl + 1);
    memcpy(p, dir, dl);
    size_t off = dl;
    if (need_sep) p[off++] = '/';
    memcpy(p + off, name, nl + 1);
    return p;
}

/* ----------------------------------------------------------------------- *
 *  uid -> name cache (getpwuid_r), guarded by a mutex
 * ----------------------------------------------------------------------- */

struct uidname {
    uid_t uid;
    char *name;
    struct uidname *next;
};
static struct uidname *g_uidname_buckets[256];
static pthread_mutex_t  g_uidname_lock = PTHREAD_MUTEX_INITIALIZER;

static const char *uid_to_name(uid_t uid) {
    unsigned b = (unsigned)uid & 0xff;
    pthread_mutex_lock(&g_uidname_lock);
    for (struct uidname *u = g_uidname_buckets[b]; u; u = u->next) {
        if (u->uid == uid) {
            const char *r = u->name;
            pthread_mutex_unlock(&g_uidname_lock);
            return r;
        }
    }
    /* resolve */
    char buf[4096];
    struct passwd pw, *res = NULL;
    char *name;
    if (getpwuid_r(uid, &pw, buf, sizeof buf, &res) == 0 && res) {
        name = xstrdup(pw.pw_name);
    } else {
        char tmp[32];
        snprintf(tmp, sizeof tmp, "%u", (unsigned)uid);
        name = xstrdup(tmp);
    }
    struct uidname *u = xmalloc(sizeof *u);
    u->uid = uid;
    u->name = name;
    u->next = g_uidname_buckets[b];
    g_uidname_buckets[b] = u;
    pthread_mutex_unlock(&g_uidname_lock);
    return name;
}

/* ----------------------------------------------------------------------- *
 *  Hardlink dedup: (dev,ino) hash set
 * ----------------------------------------------------------------------- */

struct inode_key {
    dev_t dev;
    ino_t ino;
    struct inode_key *next;
};

struct inode_set {
    struct inode_key **buckets;
    size_t nbuckets;
    size_t count;
};

static void inode_set_init(struct inode_set *s, size_t nbuckets) {
    s->nbuckets = nbuckets;
    s->count = 0;
    s->buckets = xcalloc(nbuckets, sizeof(struct inode_key *));
}

static size_t inode_hash(dev_t dev, ino_t ino, size_t nb) {
    uint64_t h = (uint64_t)ino * 1099511628211ULL;
    h ^= (uint64_t)dev * 1469598103934665603ULL;
    h ^= h >> 29;
    return (size_t)(h % nb);
}

/* returns true if newly inserted, false if already present */
static bool inode_set_add(struct inode_set *s, dev_t dev, ino_t ino) {
    size_t idx = inode_hash(dev, ino, s->nbuckets);
    for (struct inode_key *k = s->buckets[idx]; k; k = k->next) {
        if (k->ino == ino && k->dev == dev) return false;
    }
    struct inode_key *k = xmalloc(sizeof *k);
    k->dev = dev;
    k->ino = ino;
    k->next = s->buckets[idx];
    s->buckets[idx] = k;
    s->count++;
    return true;
}

static void inode_set_free(struct inode_set *s) {
    for (size_t i = 0; i < s->nbuckets; i++) {
        struct inode_key *k = s->buckets[i];
        while (k) { struct inode_key *n = k->next; free(k); k = n; }
    }
    free(s->buckets);
    s->buckets = NULL;
}

/* Global hardlink set, guarded. */
static struct inode_set g_hardlinks;
static pthread_mutex_t  g_hardlinks_lock = PTHREAD_MUTEX_INITIALIZER;

/* ----------------------------------------------------------------------- *
 *  Directory tree
 * ----------------------------------------------------------------------- */

struct node {
    char           *name;
    uint64_t        bytes;        /* subtree block usage */
    uint64_t        files;        /* subtree file count  */
    uid_t           uid;          /* owner of this directory */
    int64_t         mtime;        /* directory mtime */
    uint64_t        other_bytes;  /* bytes not represented by retained children */
    struct node    *parent;
    struct node   **children;
    size_t          nchildren;
    size_t          cap_children;
    pthread_mutex_t lock;
};

static struct node *node_new(const char *name, uid_t uid, int64_t mtime,
                             struct node *parent) {
    struct node *n = xcalloc(1, sizeof *n);
    n->name = xstrdup(name);
    n->uid = uid;
    n->mtime = mtime;
    n->parent = parent;
    pthread_mutex_init(&n->lock, NULL);
    return n;
}

static void node_add_child(struct node *parent, struct node *child) {
    pthread_mutex_lock(&parent->lock);
    if (parent->nchildren == parent->cap_children) {
        size_t nc = parent->cap_children ? parent->cap_children * 2 : 8;
        parent->children = xrealloc(parent->children, nc * sizeof(struct node *));
        parent->cap_children = nc;
    }
    parent->children[parent->nchildren++] = child;
    pthread_mutex_unlock(&parent->lock);
}

/* Add file contribution to this directory node (thread-safe). */
static void node_add_file(struct node *n, uint64_t bytes) {
    pthread_mutex_lock(&n->lock);
    n->bytes += bytes;
    n->files += 1;
    pthread_mutex_unlock(&n->lock);
}

/* Add block usage to a node without counting it as a file (used for symlinks /
 * special files, whose own blocks count toward the directory but which are not
 * regular files). */
static void node_add_self_bytes(struct node *n, uint64_t bytes) {
    pthread_mutex_lock(&n->lock);
    n->bytes += bytes;
    pthread_mutex_unlock(&n->lock);
}

/* Roll up subtree totals from leaves to root (called single-threaded after
 * the walk completes). Returns the node's final bytes/files. */
static void node_rollup(struct node *n, uint64_t *out_bytes, uint64_t *out_files) {
    uint64_t b = n->bytes;
    uint64_t f = n->files;
    for (size_t i = 0; i < n->nchildren; i++) {
        uint64_t cb = 0, cf = 0;
        node_rollup(n->children[i], &cb, &cf);
        b += cb;
        f += cf;
    }
    n->bytes = b;
    n->files = f;
    *out_bytes = b;
    *out_files = f;
}

static void node_free(struct node *n);

/* Prune children below threshold; bytes not represented by retained children
 * (direct entries plus pruned subtrees) fold into other_bytes. Must run after
 * node_rollup (final n->bytes). */
static void node_prune(struct node *n, uint64_t threshold) {
    size_t keep = 0;
    uint64_t retained_bytes = 0;
    for (size_t i = 0; i < n->nchildren; i++) {
        struct node *c = n->children[i];
        if (c->bytes >= threshold) {
            node_prune(c, threshold);
            n->children[keep++] = c;     /* retained: compact toward front */
            retained_bytes += c->bytes;
        } else {
            node_free(c);
        }
    }
    n->nchildren = keep;
    n->other_bytes = keep ? n->bytes - retained_bytes : 0;
}

static void node_free(struct node *n) {
    if (!n) return;
    for (size_t i = 0; i < n->nchildren; i++) node_free(n->children[i]);
    free(n->children);
    free(n->name);
    pthread_mutex_destroy(&n->lock);
    free(n);
}

/* ----------------------------------------------------------------------- *
 *  Per-user accumulation (by file owner uid)
 * ----------------------------------------------------------------------- */

struct user_mount {
    char    *mount;       /* mount path (not owned; points into mounts array) */
    uint64_t bytes;
    struct user_mount *next;
};

struct user_acc {
    uid_t    uid;
    uint64_t bytes;
    uint64_t files;
    struct user_mount *mounts;
    struct user_acc *next;
};

#define USER_BUCKETS 1024
static struct user_acc *g_users[USER_BUCKETS];
static pthread_mutex_t  g_users_lock = PTHREAD_MUTEX_INITIALIZER;

static void user_add(uid_t uid, const char *mount, uint64_t bytes, uint64_t files) {
    unsigned b = (unsigned)uid % USER_BUCKETS;
    pthread_mutex_lock(&g_users_lock);
    struct user_acc *u = g_users[b];
    while (u && u->uid != uid) u = u->next;
    if (!u) {
        u = xcalloc(1, sizeof *u);
        u->uid = uid;
        u->next = g_users[b];
        g_users[b] = u;
    }
    u->bytes += bytes;
    u->files += files;
    struct user_mount *m = u->mounts;
    while (m && strcmp(m->mount, mount) != 0) m = m->next;
    if (!m) {
        m = xcalloc(1, sizeof *m);
        m->mount = (char *)mount;
        m->next = u->mounts;
        u->mounts = m;
    }
    m->bytes += bytes;
    pthread_mutex_unlock(&g_users_lock);
}

/* ----------------------------------------------------------------------- *
 *  Top-N largest files (bounded min-heap) and stale list
 * ----------------------------------------------------------------------- */

struct file_rec {
    char    *path;
    uint64_t bytes;
    uid_t    uid;
    int64_t  mtime;
    int      age_days;   /* used by stale only */
};

struct minheap {
    struct file_rec *a;
    size_t n;
    size_t cap;          /* max retained (top-N) */
};

static void heap_init(struct minheap *h, size_t cap) {
    h->a = xmalloc((cap + 1) * sizeof(struct file_rec));
    h->n = 0;
    h->cap = cap;
}

static void heap_sift_up(struct minheap *h, size_t i) {
    while (i > 0) {
        size_t p = (i - 1) / 2;
        if (h->a[p].bytes <= h->a[i].bytes) break;
        struct file_rec t = h->a[p]; h->a[p] = h->a[i]; h->a[i] = t;
        i = p;
    }
}
static void heap_sift_down(struct minheap *h, size_t i) {
    for (;;) {
        size_t l = 2*i+1, r = 2*i+2, s = i;
        if (l < h->n && h->a[l].bytes < h->a[s].bytes) s = l;
        if (r < h->n && h->a[r].bytes < h->a[s].bytes) s = r;
        if (s == i) break;
        struct file_rec t = h->a[s]; h->a[s] = h->a[i]; h->a[i] = t;
        i = s;
    }
}

/* Offer a file to the bounded min-heap. Keeps the cap largest by bytes.
 * Takes ownership of `path` only if retained; otherwise frees it. */
static void heap_offer(struct minheap *h, char *path, uint64_t bytes,
                       uid_t uid, int64_t mtime, int age_days) {
    if (h->cap == 0) { free(path); return; }
    if (h->n < h->cap) {
        struct file_rec *r = &h->a[h->n];
        r->path = path; r->bytes = bytes; r->uid = uid;
        r->mtime = mtime; r->age_days = age_days;
        h->n++;
        heap_sift_up(h, h->n - 1);
    } else if (bytes > h->a[0].bytes) {
        free(h->a[0].path);
        h->a[0].path = path; h->a[0].bytes = bytes; h->a[0].uid = uid;
        h->a[0].mtime = mtime; h->a[0].age_days = age_days;
        heap_sift_down(h, 0);
    } else {
        free(path);
    }
}

static int cmp_rec_desc(const void *a, const void *b) {
    const struct file_rec *x = a, *y = b;
    if (x->bytes < y->bytes) return 1;
    if (x->bytes > y->bytes) return -1;
    return 0;
}

/* Global top-files heap + stale heap, each with its own lock. */
static struct minheap g_top;
static pthread_mutex_t g_top_lock = PTHREAD_MUTEX_INITIALIZER;
static struct minheap g_stale;
static pthread_mutex_t g_stale_lock = PTHREAD_MUTEX_INITIALIZER;

/* ----------------------------------------------------------------------- *
 *  Blocked list
 * ----------------------------------------------------------------------- */

struct blocked_rec { char *path; const char *reason; };
static struct blocked_rec g_blocked[MAX_BLOCKED];
static size_t g_blocked_n = 0;
static pthread_mutex_t g_blocked_lock = PTHREAD_MUTEX_INITIALIZER;

static void blocked_add(const char *path, int err) {
    pthread_mutex_lock(&g_blocked_lock);
    if (g_blocked_n < MAX_BLOCKED) {
        g_blocked[g_blocked_n].path = xstrdup(path);
        g_blocked[g_blocked_n].reason = (err == EACCES) ? "EACCES"
                                       : (err == EPERM)  ? "EPERM"
                                       : strerror(err);
        g_blocked_n++;
    }
    pthread_mutex_unlock(&g_blocked_lock);
}

/* ----------------------------------------------------------------------- *
 *  Work queue (directories to scan)
 * ----------------------------------------------------------------------- */

struct work_item {
    char        *path;      /* absolute path of the directory (owned) */
    struct node *node;      /* tree node for this directory */
    struct work_item *next;
};
/*
 * Note: the queue stores directory PATHS, not open fds. A worker opens the
 * directory when it dequeues an item and closes it before taking the next one,
 * so the number of concurrently-open directory fds is bounded by the thread
 * count, not by the (unbounded) queue depth. This avoids RLIMIT_NOFILE/EMFILE
 * exhaustion on large trees.
 */

struct queue {
    struct work_item *head, *tail;
    size_t            pending;   /* items in queue + items being processed */
    pthread_mutex_t   lock;
    pthread_cond_t    cond;
    bool              done;
};

static void queue_init(struct queue *q) {
    q->head = q->tail = NULL;
    q->pending = 0;
    q->done = false;
    pthread_mutex_init(&q->lock, NULL);
    pthread_cond_init(&q->cond, NULL);
}

static void queue_destroy(struct queue *q) {
    pthread_mutex_destroy(&q->lock);
    pthread_cond_destroy(&q->cond);
}

/* Atomically bump pending and enqueue (used when discovering a subdir). */
static void queue_push_new(struct queue *q, struct work_item *it) {
    pthread_mutex_lock(&q->lock);
    q->pending++;
    it->next = NULL;
    if (q->tail) q->tail->next = it;
    else q->head = it;
    q->tail = it;
    pthread_cond_signal(&q->cond);
    pthread_mutex_unlock(&q->lock);
}

/* Pop one item, blocking until one is available or the scan is done.
 * Returns NULL only when the scan is complete. */
static struct work_item *queue_pop(struct queue *q) {
    pthread_mutex_lock(&q->lock);
    for (;;) {
        if (q->head) {
            struct work_item *it = q->head;
            q->head = it->next;
            if (!q->head) q->tail = NULL;
            pthread_mutex_unlock(&q->lock);
            return it;
        }
        if (q->done) {
            pthread_mutex_unlock(&q->lock);
            return NULL;
        }
        pthread_cond_wait(&q->cond, &q->lock);
    }
}

/* Worker finished one item: decrement pending; if it reaches 0, scan done. */
static void queue_complete_one(struct queue *q) {
    pthread_mutex_lock(&q->lock);
    q->pending--;
    if (q->pending == 0) {
        q->done = true;
        pthread_cond_broadcast(&q->cond);
    }
    pthread_mutex_unlock(&q->lock);
}

/* ----------------------------------------------------------------------- *
 *  Scan context (per-target pass)
 * ----------------------------------------------------------------------- */

struct mount_info {
    char  *path;
    char  *fstype;
};

struct target_spec {
    const char *path;
    bool        guarded;
    dev_t       expected_dev;
};

struct scan_ctx {
    struct queue queue;
    dev_t        root_dev;        /* st_dev of the target root; stay within it */
    const char  *scan_root;       /* requested target path (for user_add) */
    int          stale_days;
    int          nthreads;

    /* per-pass aggregate stats (atomic via lock) */
    pthread_mutex_t stat_lock;
    uint64_t     scanned_bytes;
    uint64_t     scanned_files;
    uint64_t     scanned_dirs;
    uint64_t     errors;
};

/* ----------------------------------------------------------------------- *
 *  Worker
 * ----------------------------------------------------------------------- */

struct worker_arg {
    struct scan_ctx  *ctx;
    struct inode_set  local_links;   /* per-thread hardlink set */
    /* per-thread stat deltas to reduce lock contention */
    uint64_t bytes, files, dirs, errs;
};

static void ctx_flush_stats(struct scan_ctx *c, struct worker_arg *w) {
    pthread_mutex_lock(&c->stat_lock);
    c->scanned_bytes += w->bytes;
    c->scanned_files += w->files;
    c->scanned_dirs  += w->dirs;
    c->errors        += w->errs;
    pthread_mutex_unlock(&c->stat_lock);
    w->bytes = w->files = w->dirs = w->errs = 0;
}

static void process_dir(struct worker_arg *w, struct work_item *it) {
    struct scan_ctx *c = w->ctx;

    /* Open the directory by its absolute path. We open it here and close it at
     * the end of this function, so at most one dir fd per worker is held at a
     * time (plus transient openat-by-name checks, which we avoid). This keeps
     * concurrently-open fds bounded by the thread count. */
    int dirfd = open(it->path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dirfd == -1) {
        int e = errno;
        w->errs++;
        blocked_add(it->path, e);
        if (e == EMFILE || e == ENFILE) {
            /* Should not happen with the path-based queue; flag loudly if it
             * ever does so an undercount is never silent. */
            fprintf(stderr, "hstscan: WARNING fd exhaustion (%s) opening %s\n",
                    strerror(e), it->path);
        }
        return;
    }

    struct stat dst;
    if (fstat(dirfd, &dst) != 0) {
        w->errs++;
        blocked_add(it->path, errno);
        close(dirfd);
        return;
    }
    if (dst.st_dev != c->root_dev) {
        w->errs++;
        blocked_add(it->path, EXDEV);
        close(dirfd);
        return;
    }

    char *buf = xmalloc(GETDENTS_BUFSZ);

    for (;;) {
        long nread = syscall(SYS_getdents64, dirfd, buf, GETDENTS_BUFSZ);
        if (nread == -1) {
            w->errs++;
            blocked_add(it->path, errno);
            break;
        }
        if (nread == 0) break;

        for (long off = 0; off < nread; ) {
            struct linux_dirent64 *d = (struct linux_dirent64 *)(buf + off);
            off += d->d_reclen;
            const char *name = d->d_name;
            if (name[0] == '.' && (name[1] == '\0' ||
                (name[1] == '.' && name[2] == '\0')))
                continue;

            /* Stat every entry without following symlinks. We descend only
             * directories and never follow symlinks, but we DO count the disk
             * blocks of symlinks and special files (the symlink target path /
             * device inode occupies blocks), because `du` counts them too --
             * this is required for exact du parity. */
            struct stat st;
            if (fstatat(dirfd, name, &st, AT_SYMLINK_NOFOLLOW) != 0) {
                w->errs++;
                char *p = path_join(it->path, name);
                blocked_add(p, errno);
                free(p);
                continue;
            }

            if (S_ISDIR(st.st_mode)) {
                /* stay within one filesystem */
                if (st.st_dev != c->root_dev) continue;

                uint64_t dbytes = (uint64_t)st.st_blocks * 512;

                /* Account the child directory's own inode blocks now (we have
                 * its stat) and enqueue its PATH. The worker that dequeues it
                 * will open it (or record it as blocked if unopenable); either
                 * way the dir's own blocks are counted exactly once here. */
                struct node *child = node_new(name, st.st_uid,
                                              (int64_t)st.st_mtime, it->node);
                child->bytes += dbytes;
                node_add_child(it->node, child);

                w->dirs++;
                w->bytes += dbytes;
                user_add(st.st_uid, c->scan_root, dbytes, 0);

                struct work_item *ni = xmalloc(sizeof *ni);
                ni->path = path_join(it->path, name);
                ni->node = child;
                queue_push_new(&c->queue, ni);
            } else if (S_ISREG(st.st_mode)) {
                uint64_t fbytes = (uint64_t)st.st_blocks * 512;

                /* hardlink dedup */
                bool count_it = true;
                if (st.st_nlink > 1) {
                    if (inode_set_add(&w->local_links, st.st_dev, st.st_ino)) {
                        /* first time this thread saw it; confirm globally */
                        pthread_mutex_lock(&g_hardlinks_lock);
                        bool global_new = inode_set_add(&g_hardlinks,
                                                        st.st_dev, st.st_ino);
                        pthread_mutex_unlock(&g_hardlinks_lock);
                        count_it = global_new;
                    } else {
                        count_it = false;
                    }
                }

                if (count_it) {
                    node_add_file(it->node, fbytes);
                    w->files++;
                    w->bytes += fbytes;
                    user_add(st.st_uid, c->scan_root, fbytes, 1);

                    /* top-N */
                    char *fpath = path_join(it->path, name);
                    pthread_mutex_lock(&g_top_lock);
                    heap_offer(&g_top, xstrdup(fpath), fbytes,
                               st.st_uid, (int64_t)st.st_mtime, 0);
                    pthread_mutex_unlock(&g_top_lock);

                    /* stale: >100MB and older than stale_days */
                    if (fbytes > (uint64_t)100 * 1024 * 1024) {
                        time_t nowt = time(NULL);
                        int age = (int)((nowt - st.st_mtime) / 86400);
                        if (age >= c->stale_days) {
                            pthread_mutex_lock(&g_stale_lock);
                            heap_offer(&g_stale, xstrdup(fpath), fbytes,
                                       st.st_uid, (int64_t)st.st_mtime, age);
                            pthread_mutex_unlock(&g_stale_lock);
                        }
                    }
                    free(fpath);
                } else {
                    /* hardlink already counted; still counts as a file seen?
                     * We count distinct inodes only, so do not increment. */
                }
            } else {
                /* Symlinks and special files (socket/fifo/char/block): never
                 * followed or descended, but their own disk blocks are counted
                 * to match `du`. They are not tracked in top/stale lists.
                 * Hardlink dedup applies to special files with nlink>1 too. */
                if (S_ISLNK(st.st_mode) && st.st_blocks == 0) {
                    /* fast symlinks store the target inline (no blocks) */
                    continue;
                }
                bool count_it = true;
                if (st.st_nlink > 1 && !S_ISLNK(st.st_mode)) {
                    if (inode_set_add(&w->local_links, st.st_dev, st.st_ino)) {
                        pthread_mutex_lock(&g_hardlinks_lock);
                        bool gn = inode_set_add(&g_hardlinks,
                                                st.st_dev, st.st_ino);
                        pthread_mutex_unlock(&g_hardlinks_lock);
                        count_it = gn;
                    } else {
                        count_it = false;
                    }
                }
                if (count_it) {
                    uint64_t sbytes = (uint64_t)st.st_blocks * 512;
                    if (sbytes) {
                        node_add_self_bytes(it->node, sbytes);
                        w->bytes += sbytes;
                        user_add(st.st_uid, c->scan_root, sbytes, 0);
                    }
                }
            }
        }
    }

    free(buf);
    close(dirfd);
    if (w->bytes + w->files + w->dirs + w->errs > 100000)
        ctx_flush_stats(c, w);
}

static void *worker_main(void *argp) {
    struct worker_arg *w = argp;
    struct scan_ctx *c = w->ctx;
    for (;;) {
        struct work_item *it = queue_pop(&c->queue);
        if (!it) break;
        process_dir(w, it);
        free(it->path);
        free(it);
        queue_complete_one(&c->queue);
    }
    ctx_flush_stats(c, w);
    return NULL;
}

/* ----------------------------------------------------------------------- *
 *  Run one scan target (a single path within one filesystem)
 * ----------------------------------------------------------------------- */

/* Returns root node of the scanned tree, or NULL on failure to open root. */
static struct node *scan_target(const char *target, int nthreads, int stale_days,
                                const dev_t *expected_dev,
                                uint64_t *out_bytes, uint64_t *out_files,
                                uint64_t *out_dirs, uint64_t *out_errors) {
    struct stat rst;
    if (lstat(target, &rst) != 0 || !S_ISDIR(rst.st_mode)) {
        return NULL;
    }
    if (expected_dev && rst.st_dev != *expected_dev) {
        return NULL;
    }

    int rootfd = open(target, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (rootfd == -1) {
        blocked_add(target, errno);
        return NULL;
    }
    struct stat rootst;
    if (fstat(rootfd, &rootst) != 0) {
        blocked_add(target, errno);
        close(rootfd);
        return NULL;
    }
    dev_t opened_root_dev = rootst.st_dev;
    if (expected_dev && opened_root_dev != *expected_dev) {
        if (opened_root_dev != rst.st_dev) blocked_add(target, EXDEV);
        close(rootfd);
        return NULL;
    }
    if (opened_root_dev != rst.st_dev) {
        blocked_add(target, EXDEV);
        close(rootfd);
        return NULL;
    }
    close(rootfd);

    struct scan_ctx ctx;
    queue_init(&ctx.queue);
    ctx.root_dev = rootst.st_dev;
    ctx.scan_root = target;
    ctx.stale_days = stale_days;
    ctx.nthreads = nthreads;
    pthread_mutex_init(&ctx.stat_lock, NULL);
    ctx.scanned_bytes = ctx.scanned_files = 0;
    ctx.scanned_dirs = ctx.errors = 0;

    /* root node */
    uint64_t root_blocks = (uint64_t)rootst.st_blocks * 512;
    struct node *root = node_new(target, rootst.st_uid, (int64_t)rootst.st_mtime, NULL);
    root->bytes += root_blocks;

    ctx.scanned_dirs = 1;
    ctx.scanned_bytes = root_blocks;
    user_add(rootst.st_uid, target, root_blocks, 0);

    struct work_item *first = xmalloc(sizeof *first);
    first->path = xstrdup(target);
    first->node = root;
    queue_push_new(&ctx.queue, first);

    struct worker_arg *args = xcalloc(nthreads, sizeof *args);
    pthread_t *tids = xcalloc(nthreads, sizeof *tids);
    for (int i = 0; i < nthreads; i++) {
        args[i].ctx = &ctx;
        inode_set_init(&args[i].local_links, 4096);
        pthread_create(&tids[i], NULL, worker_main, &args[i]);
    }
    for (int i = 0; i < nthreads; i++) {
        pthread_join(tids[i], NULL);
        inode_set_free(&args[i].local_links);
    }
    free(args);
    free(tids);

    *out_bytes  = ctx.scanned_bytes;
    *out_files  = ctx.scanned_files;
    *out_dirs   = ctx.scanned_dirs;
    *out_errors = ctx.errors;

    queue_destroy(&ctx.queue);
    pthread_mutex_destroy(&ctx.stat_lock);
    return root;
}

/* ----------------------------------------------------------------------- *
 *  Mount discovery (/proc/self/mountinfo)
 * ----------------------------------------------------------------------- */

static bool fstype_is_local(const char *fs) {
    static const char *skip[] = {
        "tmpfs","devtmpfs","proc","sysfs","cgroup","cgroup2","overlay",
        "squashfs","nfs","nfs4","cifs","smb3","smbfs","autofs","rpc_pipefs",
        "bpf","pstore","efivarfs","configfs","debugfs","tracefs","mqueue",
        "hugetlbfs","devpts","securityfs","fusectl","ramfs","fuseblk",
        "binfmt_misc", NULL
    };
    for (int i = 0; skip[i]; i++) {
        if (strcmp(fs, skip[i]) == 0) return false;
    }
    /* fuse* family */
    if (strncmp(fs, "fuse", 4) == 0) return false;
    if (strncmp(fs, "smb", 3) == 0) return false;
    return true;
}

/* mountinfo field unescape: octal \040 etc. In place. */
static void unescape_mountinfo(char *s) {
    char *r = s, *w = s;
    while (*r) {
        if (r[0] == '\\' && r[1] >= '0' && r[1] <= '7' &&
            r[2] >= '0' && r[2] <= '7' && r[3] >= '0' && r[3] <= '7') {
            int v = (r[1]-'0')*64 + (r[2]-'0')*8 + (r[3]-'0');
            *w++ = (char)v;
            r += 4;
        } else {
            *w++ = *r++;
        }
    }
    *w = '\0';
}

static struct mount_info *g_mounts = NULL;
static size_t g_mounts_n = 0;

/* Parse mountinfo; populate g_mounts with local mounts. */
static void discover_mounts(void) {
    FILE *f = fopen("/proc/self/mountinfo", "r");
    if (!f) return;
    char *line = NULL;
    size_t cap = 0;
    ssize_t len;
    size_t alloc = 0;
    while ((len = getline(&line, &cap, f)) != -1) {
        /* Fields: id pid major:minor root mountpoint options... - fstype source super */
        /* Tokenize. The separator " - " precedes fstype. */
        char *mountpoint = NULL, *fstype = NULL;
        char *sep = strstr(line, " - ");
        if (!sep) continue;
        *sep = '\0';
        char *after = sep + 3;

        /* mountpoint = 5th whitespace field of the pre-separator part */
        char *save = NULL;
        char *tok = strtok_r(line, " ", &save);
        int field = 1;
        while (tok) {
            if (field == 5) { mountpoint = tok; break; }
            tok = strtok_r(NULL, " ", &save);
            field++;
        }
        /* fstype = first field after " - " */
        char *save2 = NULL;
        fstype = strtok_r(after, " ", &save2);
        if (!mountpoint || !fstype) continue;
        if (!fstype_is_local(fstype)) continue;

        if (g_mounts_n == alloc) {
            alloc = alloc ? alloc * 2 : 16;
            g_mounts = xrealloc(g_mounts, alloc * sizeof *g_mounts);
        }
        char *mp = xstrdup(mountpoint);
        unescape_mountinfo(mp);
        g_mounts[g_mounts_n].path = mp;
        g_mounts[g_mounts_n].fstype = xstrdup(fstype);
        g_mounts_n++;
    }
    free(line);
    fclose(f);
}

/* Find the mount that contains `path` (longest matching prefix). Returns
 * fstype via out_fstype (may be NULL) and the mount path. */
static const char *mount_for_path(const char *path, const char **out_fstype) {
    const char *best = "/";
    const char *bestfs = "unknown";
    size_t bestlen = 0;
    for (size_t i = 0; i < g_mounts_n; i++) {
        const char *mp = g_mounts[i].path;
        size_t ml = strlen(mp);
        if (strncmp(path, mp, ml) == 0 &&
            (path[ml] == '\0' || path[ml] == '/' || (ml == 1 && mp[0] == '/'))) {
            if (ml > bestlen) {
                bestlen = ml;
                best = mp;
                bestfs = g_mounts[i].fstype;
            }
        }
    }
    if (out_fstype) *out_fstype = bestfs;
    return best;
}

/* ----------------------------------------------------------------------- *
 *  JSON output
 * ----------------------------------------------------------------------- */

static void json_escape(FILE *f, const char *s) {
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        unsigned char c = *p;
        switch (c) {
            case '"':  fputs("\\\"", f); break;
            case '\\': fputs("\\\\", f); break;
            case '\b': fputs("\\b", f);  break;
            case '\f': fputs("\\f", f);  break;
            case '\n': fputs("\\n", f);  break;
            case '\r': fputs("\\r", f);  break;
            case '\t': fputs("\\t", f);  break;
            default:
                if (c < 0x20) {
                    fprintf(f, "\\u%04x", c);
                } else if (c >= 0x80) {
                    /* Keep generated JSON strictly UTF-8 parseable even when
                     * filesystem names contain arbitrary non-UTF8 bytes.
                     * Escaping every high byte as U+00XX preserves byte-level
                     * observability without emitting invalid JSON text. */
                    fprintf(f, "\\u%04x", c);
                } else {
                    fputc(c, f);
                }
        }
    }
    fputc('"', f);
}

static void emit_node(FILE *f, struct node *n, int indent) {
    (void)indent;
    fputc('{', f);
    fputs("\"name\":", f); json_escape(f, n->name);
    fputs(",\"kind\":\"directory\"", f);
    fprintf(f, ",\"bytes\":%llu", (unsigned long long)n->bytes);
    fprintf(f, ",\"files\":%llu", (unsigned long long)n->files);
    fprintf(f, ",\"uid\":%u", (unsigned)n->uid);
    fprintf(f, ",\"mtime\":%lld", (long long)n->mtime);
    fprintf(f, ",\"other_bytes\":%llu", (unsigned long long)n->other_bytes);
    if (n->nchildren > 0) {
        fputs(",\"children\":[", f);
        for (size_t i = 0; i < n->nchildren; i++) {
            if (i) fputc(',', f);
            emit_node(f, n->children[i], indent + 1);
        }
        fputc(']', f);
    }
    fputc('}', f);
}

/* ----------------------------------------------------------------------- *
 *  Main
 * ----------------------------------------------------------------------- */

struct mount_result {
    char    *path;
    char    *fstype;
    struct node *tree;
    uint64_t scanned_bytes, scanned_files, scanned_dirs, errors;
    /* df */
    uint64_t df_total, df_used, df_avail;
    int      df_use_pct;
};

static void fill_df(const char *path, struct mount_result *mr) {
    struct statvfs vfs;
    if (statvfs(path, &vfs) == 0) {
        uint64_t bs = vfs.f_frsize ? vfs.f_frsize : vfs.f_bsize;
        mr->df_total = (uint64_t)vfs.f_blocks * bs;
        mr->df_avail = (uint64_t)vfs.f_bavail * bs;
        uint64_t free_all = (uint64_t)vfs.f_bfree * bs;
        mr->df_used  = mr->df_total - free_all;
        uint64_t denom = mr->df_used + mr->df_avail;
        mr->df_use_pct = denom ? (int)((mr->df_used * 100 + denom - 1) / denom) : 0;
    }
}

static void usage(const char *prog) {
    fprintf(stderr,
        "Usage: %s [--threads N] [--prune-home MB] [--prune-data MB]\n"
        "          [--top N] [--stale-days D] [--out PATH] [--out-dir DIR]\n"
        "          [--target PATH MAJOR:MINOR ...] [PATH ...]\n"
        "       --target may be repeated; each consumes PATH and MAJOR:MINOR.\n"
        "       Do not mix guarded --target arguments with positional PATH targets.\n"
        "       default output: data/<hostname>.json relative to the current directory\n",
        prog);
}

static bool parse_u32_decimal(const char *s, const char **endp, unsigned *out) {
    if (!s || *s < '0' || *s > '9') return false;
    errno = 0;
    char *end = NULL;
    unsigned long long v = strtoull(s, &end, 10);
    if (errno == ERANGE || end == s || v > UINT32_MAX) return false;
    *endp = end;
    *out = (unsigned)v;
    return true;
}

static bool parse_major_minor_dev(const char *s, dev_t *out) {
    unsigned maj = 0, min = 0;
    const char *p = NULL;
    if (!parse_u32_decimal(s, &p, &maj)) return false;
    if (*p != ':') return false;
    if (!parse_u32_decimal(p + 1, &p, &min)) return false;
    if (*p != '\0') return false;

    dev_t dev = makedev(maj, min);
    if ((unsigned)major(dev) != maj || (unsigned)minor(dev) != min) return false;
    *out = dev;
    return true;
}

/* Defense in depth: raise the open-file soft limit to the hard limit (and, if
 * running as root, raise the hard limit too). The path-based queue already
 * bounds concurrently-open fds to ~thread count, so this is belt-and-braces.
 * Failures are ignored. */
static void raise_nofile_limit(void) {
    struct rlimit rl;
    if (getrlimit(RLIMIT_NOFILE, &rl) != 0) return;
    if (geteuid() == 0) {
        /* As root we may also raise the hard limit. */
        rlim_t want = (rlim_t)1 << 20;   /* 1,048,576 */
        if (rl.rlim_max == RLIM_INFINITY || rl.rlim_max < want)
            rl.rlim_max = (rl.rlim_max == RLIM_INFINITY) ? rl.rlim_max : want;
    }
    rl.rlim_cur = rl.rlim_max;
    (void)setrlimit(RLIMIT_NOFILE, &rl);
}

int main(int argc, char **argv) {
    raise_nofile_limit();
    int nthreads = (int)sysconf(_SC_NPROCESSORS_ONLN);
    if (nthreads < 1) nthreads = 4;
    int prune_home_mb = DEFAULT_PRUNE_HOME_MB;
    int prune_data_mb = DEFAULT_PRUNE_DATA_MB;
    int top_n = DEFAULT_TOP;
    int stale_days = DEFAULT_STALE_DAYS;
    const char *out_path = NULL;
    const char *out_dir = "data";

    struct target_spec targets_buf[64];
    int ntargets = 0;
    bool guarded_mode = false;
    bool positional_mode = false;

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (strcmp(a, "--threads") == 0 && i+1 < argc) {
            nthreads = atoi(argv[++i]); if (nthreads < 1) nthreads = 1;
        } else if (strcmp(a, "--prune-home") == 0 && i+1 < argc) {
            prune_home_mb = atoi(argv[++i]);
        } else if (strcmp(a, "--prune-data") == 0 && i+1 < argc) {
            prune_data_mb = atoi(argv[++i]);
        } else if (strcmp(a, "--top") == 0 && i+1 < argc) {
            top_n = atoi(argv[++i]); if (top_n < 0) top_n = 0;
        } else if (strcmp(a, "--stale-days") == 0 && i+1 < argc) {
            stale_days = atoi(argv[++i]);
        } else if (strcmp(a, "--out") == 0 && i+1 < argc) {
            out_path = argv[++i];
        } else if (strcmp(a, "--out-dir") == 0 && i+1 < argc) {
            out_dir = argv[++i];
        } else if (strcmp(a, "--target") == 0) {
            if (positional_mode || i + 2 >= argc) {
                usage(argv[0]); return 2;
            }
            const char *path = argv[++i];
            const char *major_minor = argv[++i];
            dev_t expected_dev;
            if (path[0] != '/' || !parse_major_minor_dev(major_minor, &expected_dev)) {
                usage(argv[0]); return 2;
            }
            guarded_mode = true;
            if (ntargets >= 64) {
                usage(argv[0]); return 2;
            }
            targets_buf[ntargets].path = path;
            targets_buf[ntargets].guarded = true;
            targets_buf[ntargets].expected_dev = expected_dev;
            ntargets++;
        } else if (strcmp(a, "-h") == 0 || strcmp(a, "--help") == 0) {
            usage(argv[0]); return 0;
        } else if (a[0] == '-') {
            fprintf(stderr, "hstscan: unknown option %s\n", a);
            usage(argv[0]); return 2;
        } else {
            if (guarded_mode) {
                usage(argv[0]); return 2;
            }
            positional_mode = true;
            if (ntargets >= 64) {
                usage(argv[0]); return 2;
            }
            targets_buf[ntargets].path = a;
            targets_buf[ntargets].guarded = false;
            targets_buf[ntargets].expected_dev = 0;
            ntargets++;
        }
    }

    const struct target_spec default_targets[] = {
        { "/", false, 0 }, { "/data", false, 0 },
        { "/data1", false, 0 }, { "/data3", false, 0 }
    };
    const struct target_spec *targets;
    int target_count;
    if (ntargets > 0) {
        targets = targets_buf;
        target_count = ntargets;
    } else {
        targets = default_targets;
        target_count = 4;
    }

    inode_set_init(&g_hardlinks, 1 << 16);
    heap_init(&g_top, (size_t)top_n);
    heap_init(&g_stale, MAX_STALE);
    discover_mounts();

    char hostname[256];
    if (gethostname(hostname, sizeof hostname) != 0)
        strcpy(hostname, "unknown");
    hostname[sizeof hostname - 1] = '\0';

    bool run_as_root = (geteuid() == 0);

    time_t scan_started = time(NULL);
    double t0 = now_monotonic();

    /* Run each target; collect results. Targets that don't exist are skipped. */
    struct mount_result *results = xcalloc(target_count, sizeof *results);
    int nresults = 0;

    for (int i = 0; i < target_count; i++) {
        const char *tg = targets[i].path;
        struct stat tst;
        if (lstat(tg, &tst) != 0) {
            continue;  /* target absent (e.g. /data not present): skip silently */
        }
        const char *fstype = NULL;
        (void)mount_for_path(tg, &fstype);

        uint64_t b=0,fl=0,d=0,e=0;
        struct node *tree = scan_target(tg, nthreads, stale_days,
                                        targets[i].guarded ? &targets[i].expected_dev : NULL,
                                        &b, &fl, &d, &e);
        if (!tree) continue;

        /* Roll up subtree totals then prune. */
        uint64_t rb=0, rf=0;
        node_rollup(tree, &rb, &rf);

        /* Prune threshold: "/" target uses prune-home, others prune-data. */
        int prune_mb = (strcmp(tg, "/") == 0) ? prune_home_mb : prune_data_mb;
        uint64_t threshold = (uint64_t)prune_mb * 1024 * 1024;
        node_prune(tree, threshold);

        struct mount_result *mr = &results[nresults++];
        mr->path = xstrdup(tg);
        mr->fstype = xstrdup(fstype ? fstype : "unknown");
        mr->tree = tree;
        mr->scanned_bytes = rb;
        mr->scanned_files = rf;
        mr->scanned_dirs  = d;
        mr->errors        = e;
        fill_df(tg, mr);
    }

    double dur = now_monotonic() - t0;

    /* ---- Build output path ---- */
    char default_out[1024];
    if (!out_path) {
        snprintf(default_out, sizeof default_out, "%s/%s.json", out_dir, hostname);
        out_path = default_out;
    }
    char tmp_path[1100];
    snprintf(tmp_path, sizeof tmp_path, "%s.tmp", out_path);

    FILE *f = fopen(tmp_path, "w");
    if (!f) {
        fprintf(stderr, "hstscan: cannot open %s: %s\n", tmp_path, strerror(errno));
        return 1;
    }

    fputs("{\n", f);
    fprintf(f, "\"schema_version\":%d,\n", SCHEMA_VERSION);
    fputs("\"hostname\":", f); json_escape(f, hostname); fputs(",\n", f);
    fputs("\"scanner_version\":\"" SCANNER_VERSION "\",\n", f);
    fprintf(f, "\"scan_started_unix\":%lld,\n", (long long)scan_started);
    fprintf(f, "\"scan_duration_sec\":%.3f,\n", dur);
    fprintf(f, "\"run_as_root\":%s,\n", run_as_root ? "true" : "false");

    /* mounts */
    fputs("\"mounts\":[", f);
    for (int i = 0; i < nresults; i++) {
        struct mount_result *mr = &results[i];
        if (i) fputc(',', f);
        fputs("\n{", f);
        fputs("\"path\":", f); json_escape(f, mr->path);
        fputs(",\"fstype\":", f); json_escape(f, mr->fstype);
        fprintf(f, ",\"df_total\":%llu", (unsigned long long)mr->df_total);
        fprintf(f, ",\"df_used\":%llu", (unsigned long long)mr->df_used);
        fprintf(f, ",\"df_avail\":%llu", (unsigned long long)mr->df_avail);
        fprintf(f, ",\"df_use_pct\":%d", mr->df_use_pct);
        fprintf(f, ",\"scanned_bytes\":%llu", (unsigned long long)mr->scanned_bytes);
        fprintf(f, ",\"scanned_files\":%llu", (unsigned long long)mr->scanned_files);
        fprintf(f, ",\"scanned_dirs\":%llu", (unsigned long long)mr->scanned_dirs);
        fprintf(f, ",\"errors\":%llu", (unsigned long long)mr->errors);
        fputs(",\"tree\":", f);
        emit_node(f, mr->tree, 0);
        fputc('}', f);
    }
    fputs("\n],\n", f);

    /* users */
    fputs("\"users\":[", f);
    bool first_user = true;
    for (size_t b = 0; b < USER_BUCKETS; b++) {
        for (struct user_acc *u = g_users[b]; u; u = u->next) {
            if (!first_user) fputc(',', f);
            first_user = false;
            fputs("\n{", f);
            fprintf(f, "\"uid\":%u", (unsigned)u->uid);
            fputs(",\"name\":", f); json_escape(f, uid_to_name(u->uid));
            fprintf(f, ",\"bytes\":%llu", (unsigned long long)u->bytes);
            fprintf(f, ",\"files\":%llu", (unsigned long long)u->files);
            fputs(",\"by_mount\":{", f);
            bool fm = true;
            for (struct user_mount *m = u->mounts; m; m = m->next) {
                if (!fm) fputc(',', f);
                fm = false;
                json_escape(f, m->mount);
                fprintf(f, ":%llu", (unsigned long long)m->bytes);
            }
            fputs("}}", f);
        }
    }
    fputs("\n],\n", f);

    /* top_files (sorted desc) */
    qsort(g_top.a, g_top.n, sizeof(struct file_rec), cmp_rec_desc);
    fputs("\"top_files\":[", f);
    for (size_t i = 0; i < g_top.n; i++) {
        struct file_rec *r = &g_top.a[i];
        if (i) fputc(',', f);
        fputs("\n{", f);
        fputs("\"path\":", f); json_escape(f, r->path);
        fputs(",\"kind\":\"file\"", f);
        fprintf(f, ",\"bytes\":%llu", (unsigned long long)r->bytes);
        fprintf(f, ",\"uid\":%u", (unsigned)r->uid);
        fputs(",\"owner\":", f); json_escape(f, uid_to_name(r->uid));
        fprintf(f, ",\"mtime\":%lld", (long long)r->mtime);
        fputc('}', f);
    }
    fputs("\n],\n", f);

    /* stale (sorted desc by bytes) */
    qsort(g_stale.a, g_stale.n, sizeof(struct file_rec), cmp_rec_desc);
    fputs("\"stale\":[", f);
    for (size_t i = 0; i < g_stale.n; i++) {
        struct file_rec *r = &g_stale.a[i];
        if (i) fputc(',', f);
        fputs("\n{", f);
        fputs("\"path\":", f); json_escape(f, r->path);
        fputs(",\"kind\":\"file\"", f);
        fprintf(f, ",\"bytes\":%llu", (unsigned long long)r->bytes);
        fprintf(f, ",\"uid\":%u", (unsigned)r->uid);
        fputs(",\"owner\":", f); json_escape(f, uid_to_name(r->uid));
        fprintf(f, ",\"mtime\":%lld", (long long)r->mtime);
        fprintf(f, ",\"age_days\":%d", r->age_days);
        fputc('}', f);
    }
    fputs("\n],\n", f);

    /* blocked */
    fputs("\"blocked\":[", f);
    for (size_t i = 0; i < g_blocked_n; i++) {
        if (i) fputc(',', f);
        fputs("\n{", f);
        fputs("\"path\":", f); json_escape(f, g_blocked[i].path);
        fputs(",\"reason\":", f); json_escape(f, g_blocked[i].reason);
        fputc('}', f);
    }
    fputs("\n]\n", f);

    fputs("}\n", f);

    if (fclose(f) != 0) {
        fprintf(stderr, "hstscan: write error on %s: %s\n", tmp_path, strerror(errno));
        return 1;
    }
    if (rename(tmp_path, out_path) != 0) {
        fprintf(stderr, "hstscan: rename %s -> %s failed: %s\n",
                tmp_path, out_path, strerror(errno));
        return 1;
    }

    /* ---- stderr summary ---- */
    uint64_t tot_bytes = 0, tot_files = 0, tot_errors = 0;
    for (int i = 0; i < nresults; i++) {
        tot_bytes  += results[i].scanned_bytes;
        tot_files  += results[i].scanned_files;
        tot_errors += results[i].errors;
    }
    fprintf(stderr,
        "hstscan: %d mount(s) scanned, %llu bytes, %llu files, %.2fs, %llu error(s) -> %s\n",
        nresults,
        (unsigned long long)tot_bytes,
        (unsigned long long)tot_files,
        dur,
        (unsigned long long)tot_errors,
        out_path);

    /* free (best-effort; OS reclaims anyway) */
    for (int i = 0; i < nresults; i++) {
        node_free(results[i].tree);
        free(results[i].path);
        free(results[i].fstype);
    }
    free(results);

    return 0;
}
