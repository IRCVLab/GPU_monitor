#!/bin/bash -p
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
usage() {
  printf 'Usage: %s [--dry-run] [--prefix ABSOLUTE] --dev-public-key KEY [--live-public-key KEY]\n' "$0" >&2
  exit 2
}

dry_run=false
prefix=/
dev_key=
live_key=
while (($#)); do
  case "$1" in
    --dry-run) dry_run=true; shift ;;
    --prefix) [[ $# -ge 2 ]] || usage; prefix=$2; shift 2 ;;
    --dev-public-key) [[ $# -ge 2 ]] || usage; dev_key=$2; shift 2 ;;
    --live-public-key) [[ $# -ge 2 ]] || usage; live_key=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$dev_key" ]] || fail "--dev-public-key is required"
if [[ "$prefix" != / && "$dry_run" == false ]]; then
  fail "--prefix is supported only with --dry-run"
fi

normalize_key() {
  local key=$1
  [[ "$key" != *$'\n'* && "$key" =~ ^(ssh-ed25519|sk-ssh-ed25519@openssh.com)\ [A-Za-z0-9+/=]+(\ .*)?$ ]] ||
    fail "public key must be one plain Ed25519 key without options"
  local key_type=${key%% *} remainder=${key#* } key_blob
  key_blob=${remainder%% *}
  printf '%s %s\n' "$key_type" "$key_blob"
}
dev_key=$(normalize_key "$dev_key")
if [[ -n "$live_key" ]]; then
  live_key=$(normalize_key "$live_key")
  [[ "$live_key" != "$dev_key" ]] || fail "dev and live public key material must be distinct"
fi

canonical_prefix=$(/usr/bin/python3 - "$prefix" "$dry_run" "$(id -u)" <<'PY'
import os, pathlib, sys
raw, dry, uid = sys.argv[1], sys.argv[2] == "true", int(sys.argv[3])
def reject(message):
    print("ERROR: " + message, file=sys.stderr); raise SystemExit(1)
if "\n" in raw or not raw.startswith("/"): reject("prefix must be absolute and single-line")
if os.path.normpath(raw) != raw or (raw == "/" and dry): reject("prefix must be canonical and non-root")
path = pathlib.Path(raw)
cursor = pathlib.Path("/")
for part in path.parts[1:]:
    cursor = cursor / part
    if cursor.is_symlink(): reject("prefix must not contain symlinks")
existing = path
while not existing.exists():
    if existing.parent == existing: reject("no existing prefix ancestor")
    existing = existing.parent
resolved = path.resolve(strict=False)
if str(resolved) != raw or (str(resolved) == "/" and dry): reject("prefix alias is forbidden")
owner = existing.stat().st_uid
required_owner = uid if dry else 0
if owner != required_owner: reject("prefix root is not owned by installer identity")
print(raw)
PY
) || exit 1
prefix=$canonical_prefix

if [[ "$dry_run" == false && "$(id -u)" -ne 0 ]]; then
  fail "real installation requires root"
fi

installed_live_authorization="$prefix/home/gpu-deploy-live/.ssh/authorized_keys"
if [[ -z "$live_key" && -f "$installed_live_authorization" ]]; then
  installed_live_line=$(<"$installed_live_authorization")
  installed_live_prefix='restrict,command="/usr/local/libexec/gpu-monitor-deploy-command live" '
  [[ "$installed_live_line" == "$installed_live_prefix"* ]] ||
    fail "installed live authorized key has unexpected format"
  installed_live_key=$(normalize_key "${installed_live_line#"$installed_live_prefix"}")
  [[ "$installed_live_key" != "$dev_key" ]] ||
    fail "dev and installed live public key material must be distinct"
fi

script_dir=${BASH_SOURCE[0]%/*}
[[ "$script_dir" != "${BASH_SOURCE[0]}" ]] || script_dir=.
script_dir=$(cd -- "$script_dir" && /bin/pwd -P)

install_file() {
  local mode=$1 source=$2 destination=$3
  mkdir -p "${destination%/*}"
  /usr/bin/install -m "$mode" "$source" "$destination"
}

ensure_identity() {
  local user=$1 home=$2 shell=$3 system_home=$4
  local group_record passwd_record actual_uid actual_gid actual_home actual_shell expected_gid shadow_record
  if group_record=$(getent group "$user"); then
    expected_gid=${group_record#*:*:}; expected_gid=${expected_gid%%:*}
  else
    /usr/sbin/groupadd --system "$user"
    group_record=$(getent group "$user")
    expected_gid=${group_record#*:*:}; expected_gid=${expected_gid%%:*}
  fi
  if passwd_record=$(getent passwd "$user"); then
    IFS=: read -r _ _ actual_uid actual_gid _ actual_home actual_shell <<< "$passwd_record"
    [[ "$actual_uid" != 0 && "$actual_gid" != 0 ]] || fail "existing $user has root uid/gid"
    [[ "$actual_home" == "$home" ]] || fail "existing $user has unexpected home"
    [[ "$actual_shell" == "$shell" ]] || fail "existing $user has unexpected shell"
    [[ "$actual_gid" == "$expected_gid" ]] || fail "existing $user has unexpected primary group"
  else
    /usr/sbin/useradd --system --gid "$user" --home-dir "$home" --shell "$shell" "$user"
  fi
  /usr/sbin/usermod -L "$user"
}

ensure_deploy_and_runtime_users() {
  local environment=$1 deploy_user="gpu-deploy-$1" runtime_user="gpu-monitor-$1"
  ensure_identity "$deploy_user" "/home/$deploy_user" /bin/sh true
  ensure_identity "$runtime_user" "/var/lib/gpu-monitor/$environment" /usr/sbin/nologin true
}

validate_identity_separation() {
  local environment=$1 deploy_user="gpu-deploy-$1" runtime_user="gpu-monitor-$1"
  /usr/bin/python3 - "$deploy_user" "$runtime_user" <<'PY'
import grp, pwd, sys
deploy, runtime = sys.argv[1:]
d = pwd.getpwnam(deploy); r = pwd.getpwnam(runtime)
if 0 in (d.pw_uid, d.pw_gid, r.pw_uid, r.pw_gid): raise SystemExit("ERROR: deploy/runtime ids must be nonzero")
if d.pw_uid == r.pw_uid or d.pw_gid == r.pw_gid: raise SystemExit("ERROR: deploy/runtime ids must be distinct")
for group in grp.getgrall():
    members = set(group.gr_mem)
    if deploy in members and runtime in members:
        raise SystemExit("ERROR: deploy/runtime users share supplemental group " + group.gr_name)
PY
}


rewrite_reserved_env() {
  local path=$1; shift
  mkdir -p "${path%/*}"
  /usr/bin/python3 - "$path" "$@" <<'PY'
import os, sys
path, required = sys.argv[1], sys.argv[2:]
canonical = {}
order = []
for item in required:
    key, value = item.split("=", 1)
    key_bytes = key.encode("ascii")
    canonical[key_bytes] = item.encode("ascii")
    order.append(key_bytes)
try:
    with open(path, "rb") as source:
        original = source.read()
except FileNotFoundError:
    original = b""
seen = set()
rewritten = []
for line in original.splitlines(keepends=True):
    body = line.rstrip(b"\r\n")
    ending = line[len(body):]
    key = body.split(b"=", 1)[0] if b"=" in body else None
    if key not in canonical:
        rewritten.append(line)
        continue
    if key in seen:
        continue
    seen.add(key)
    rewritten.append(canonical[key] + ending)
missing = [key for key in order if key not in seen]
if missing:
    if rewritten and not rewritten[-1].endswith((b"\n", b"\r")):
        rewritten.append(b"\n")
    rewritten.extend(canonical[key] + b"\n" for key in missing)
updated = b"".join(rewritten)
if updated != original or not os.path.exists(path):
    temporary = path + ".tmp." + str(os.getpid())
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
os.chmod(path, 0o640)
PY
}

reconcile_deployment_trees() {
  local releases_root=$1 generations_root=$2
  /usr/bin/python3 - "$releases_root" "$generations_root" <<'PY'
import os, pathlib, stat, sys
releases, generations = map(pathlib.Path, sys.argv[1:])

def reconcile_tree(root, directory_mode, file_mode, executable_mode):
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root, followlinks=False):
        os.chmod(current, directory_mode)
        for name in files:
            path = pathlib.Path(current) / name
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode):
                os.chmod(path, executable_mode if mode & stat.S_IXUSR else file_mode)

for child in releases.iterdir():
    if not child.is_dir() or child.is_symlink():
        continue
    if child.name.startswith(".release-"):
        reconcile_tree(child, 0o700, 0o600, 0o700)
    else:
        reconcile_tree(child, 0o550, 0o440, 0o550)

for child in generations.iterdir():
    if child.is_dir() and not child.is_symlink():
        reconcile_tree(child, 0o550, 0o440, 0o550)
PY
}

for environment in dev live; do
  deploy_user="gpu-deploy-$environment"
  runtime_user="gpu-monitor-$environment"
  home="$prefix/home/$deploy_user"
  ssh_dir="$home/.ssh"
  env_root="$prefix/srv/gpu-monitor/$environment"
  shared_root="$prefix/var/lib/gpu-monitor/$environment"
  lock_root="$prefix/var/lock/gpu-monitor/$environment"
  mkdir -p "$ssh_dir" "$env_root"/{incoming,releases,tmp,generations} "$shared_root" "$lock_root"
  chmod 0755 "$home"
  chmod 0700 "$ssh_dir" "$env_root/incoming" "$lock_root"
  chmod 0750 "$env_root"
  chmod 2700 "$env_root/tmp"
  chmod 2750 "$env_root/releases" "$env_root/generations"
  chmod 0750 "$shared_root"
  : > "$lock_root/activation.lock"
  chmod 0600 "$lock_root/activation.lock"
  if [[ "$dry_run" == false ]]; then
    ensure_deploy_and_runtime_users "$environment"
    validate_identity_separation "$environment"
    chown -R root:root "$home"
    chown "$deploy_user:$runtime_user" "$env_root" "$env_root/releases" "$env_root/generations"
    chown -R "$deploy_user:$runtime_user" "$env_root/releases" "$env_root/generations" "$env_root/tmp"
    chown -R "$deploy_user:$deploy_user" "$env_root/incoming" "$lock_root"
    [[ ! -e "$env_root/deployments.jsonl" ]] ||
      chown "$deploy_user:$deploy_user" "$env_root/deployments.jsonl"
    chmod 0750 "$env_root"
    chmod 2700 "$env_root/tmp"
    chmod 2750 "$env_root/releases" "$env_root/generations"
    chmod 0700 "$env_root/incoming" "$lock_root"
    [[ ! -e "$env_root/deployments.jsonl" ]] || chmod 0600 "$env_root/deployments.jsonl"
    chown -R "$runtime_user:$runtime_user" "$shared_root"
  fi
  reconcile_deployment_trees "$env_root/releases" "$env_root/generations"
done

write_authorized_key() {
  local environment=$1 key=$2 user="gpu-deploy-$1"
  local path="$prefix/home/$user/.ssh/authorized_keys"
  local temporary="${path}.tmp"
  printf 'restrict,command="/usr/local/libexec/gpu-monitor-deploy-command %s" %s\n' "$environment" "$key" > "$temporary"
  chmod 0600 "$temporary"
  mv "$temporary" "$path"
  if [[ "$dry_run" == false ]]; then chown root:root "$path"; fi
}
write_authorized_key dev "$dev_key"
[[ -z "$live_key" ]] || write_authorized_key live "$live_key"

install_file 0755 "$script_dir/gpu-monitor-deploy-command" "$prefix/usr/local/libexec/gpu-monitor-deploy-command"
install_file 0755 "$script_dir/activate-release.sh" "$prefix/usr/local/libexec/activate-release.sh"
install_file 0755 "$script_dir/health-check.sh" "$prefix/usr/local/libexec/health-check.sh"
install_file 0755 "$script_dir/gpu-monitor-restart-broker" "$prefix/usr/local/libexec/gpu-monitor-restart-broker"
for unit in "$script_dir"/systemd/*.service; do
  install_file 0644 "$unit" "$prefix/etc/systemd/system/${unit##*/}"
done
install_file 0440 "$script_dir/sudoers/gpu-monitor-deploy-dev" "$prefix/etc/sudoers.d/gpu-monitor-deploy-dev"
install_file 0440 "$script_dir/sudoers/gpu-monitor-deploy-live" "$prefix/etc/sudoers.d/gpu-monitor-deploy-live"

rewrite_reserved_env "$prefix/etc/gpu-monitor/dev.env" \
  GPU_MONITOR_BACKEND_PORT=8101 PORT=5174 GPU_MONITOR_SHARED_DIR=/var/lib/gpu-monitor/dev
rewrite_reserved_env "$prefix/etc/gpu-monitor/live.env" \
  GPU_MONITOR_BACKEND_PORT=8001 GPU_MONITOR_BRIDGE_PORT=8000 PORT=5173 GPU_MONITOR_SHARED_DIR=/var/lib/gpu-monitor/live
if [[ "$dry_run" == false ]]; then
  chown root:gpu-deploy-dev "$prefix/etc/gpu-monitor/dev.env"
  chown root:gpu-deploy-live "$prefix/etc/gpu-monitor/live.env"
  chmod 0640 "$prefix/etc/gpu-monitor/"*.env
  chown root:root "$prefix/etc/sudoers.d/gpu-monitor-deploy-"*
  /usr/sbin/visudo -cf "$prefix/etc/sudoers.d/gpu-monitor-deploy-dev"
  /usr/sbin/visudo -cf "$prefix/etc/sudoers.d/gpu-monitor-deploy-live"
  /usr/bin/systemctl daemon-reload
fi

printf 'Installed isolated GPU deploy/runtime boundaries under %s (dry-run=%s); no services enabled or started.\n' "$prefix" "$dry_run"
