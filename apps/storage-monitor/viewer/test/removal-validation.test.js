import test from 'node:test';
import assert from 'node:assert/strict';
import * as viewer from '../removal.js';
const GiB = 1024 ** 3;
function makeCleanupSnapshot() {
  return {
    server_id: "alpha-1",
    hostname: "alpha",
    scan_finished_unix: 1721390400,
    selected_roots: [
      {
        mount_id: "rootfs",
        mount_root: "/",
        mountpoint: "/",
        scan_root: "/home",
        status: "partial",
      },
      {
        mount_id: "data",
        mount_root: "/",
        mountpoint: "/data",
        scan_root: "/data",
        status: "complete",
      },
      {
        mount_id: "archive",
        mount_root: "/",
        mountpoint: "/archive",
        scan_root: "/archive",
        status: "failed",
      },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/home", scan_root: "/home" },
      { mount_id: "data", path: "/data", scan_root: "/data" },
    ],
  };
}

function testCleanupCommandSafetyContracts() {
  const {
    shellQuote,
    isCanonicalCleanupPath,
    cleanupPathWithinRoot,
    cleanupLongestMatchingRoot,
    validateCleanupSelection,
    buildCleanupCommandPlan,
  } = viewer;

  assert.strictEqual(shellQuote("/data/a b/it's.txt"), "'/data/a b/it'\"'\"'s.txt'");
  assert.equal(isCanonicalCleanupPath("/data/보고서.txt"), true);
  assert.equal(isCanonicalCleanupPath("/data/bad-\uFFFD.txt"), false,
    "replacement text cannot safely identify the original filename");
  assert.strictEqual(typeof cleanupPathWithinRoot, "function", "root containment helper must be exposed for boundary tests");
  assert.strictEqual(typeof cleanupLongestMatchingRoot, "function", "longest-root matching helper must be exposed for boundary tests");
  assert.strictEqual(typeof validateCleanupSelection, "function", "selection validation must be centralized as a pure function");
  assert.strictEqual(typeof buildCleanupCommandPlan, "function", "fixed command template generation must be centralized as a pure function");

  const snapshot = makeCleanupSnapshot();
  const candidate = {
    path: "/data/projects/-dash * glob \"quotes\" 유니코드's",
    kind: "directory",
    source: "treemap",
  };
  const validation = validateCleanupSelection(snapshot, candidate);
  assert.deepStrictEqual({
    accepted: validation.accepted,
    kind: validation.kind,
    path: validation.path,
    scanRoot: validation.scanRoot,
  }, {
    accepted: true,
    kind: "directory",
    path: "/data/projects/-dash * glob \"quotes\" 유니코드's",
    scanRoot: "/data",
  }, "valid file/directory selections must stay opaque and stay anchored to the matched selected scan root");

  const plan = buildCleanupCommandPlan(snapshot, candidate, {});
  assert.strictEqual(plan.inspectionCommands, undefined, "selection must produce removal output only");
  assert.strictEqual(plan.destructiveVisible, undefined, "removal output must not depend on reveal state");
  assert.strictEqual(plan.destructiveCommand.command, "sudo rm -rf --one-file-system --preserve-root=all -- '/data/projects/-dash * glob \"quotes\" 유니코드'\"'\"'s'");

  const stalePlan = buildCleanupCommandPlan(snapshot, { path: "/data/old.bin", kind: "file", source: "stale" }, {});
  assert.strictEqual(stalePlan.destructiveCommand.command, "sudo rm -f -- '/data/old.bin'");

  const rejectedCases = [
    [{ path: "/data/link", kind: "symlink" }, "unsupported_kind"],
    [{ path: "/data/device", kind: "other" }, "unsupported_kind"],
    [{ path: "/data/missing-kind" }, "missing_kind"],
    [{ path: "/" , kind: "directory" }, "root_path"],
    [{ path: "/data", kind: "directory" }, "scan_root"],
    [{ path: "/home", kind: "directory" }, "scan_root"],
    [{ path: "/archive/project", kind: "directory" }, "selected_root_status"],
    [{ path: "/tmp", kind: "directory" }, "top_level_root"],
    [{ path: "/srv/projects/model.ckpt", kind: "file" }, "outside_selected_roots"],
    [{ path: "relative/path", kind: "file" }, "noncanonical_path"],
    [{ path: "/data/../escape", kind: "file" }, "noncanonical_path"],
    [{ path: "/data/control\nchar", kind: "file" }, "control_character"],
    // The collector accepts any UTF-8 path without NUL (§2.2); removal is where these are refused (§4.4).
    [{ path: "/data/a\tb", kind: "file" }, "control_character"],
    [{ path: "/data/esc\u001b[0m", kind: "directory" }, "control_character"],
  ];
  for (const [input, code] of rejectedCases) {
    const rejected = validateCleanupSelection(snapshot, input);
    assert.strictEqual(rejected.accepted, false, `${input.path || input.kind} must be rejected`);
    assert.strictEqual(rejected.reason.code, code, `${input.path || input.kind} must expose a bounded rejection code`);
  }

  const rootScopedSnapshot = {
    server_id: "root-scan",
    selected_roots: [
      { mount_id: "rootfs", mount_root: "/", mountpoint: "/", scan_root: "/", status: "complete" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", scan_root: "/" },
    ],
  };
  assert.strictEqual(cleanupPathWithinRoot("/home/user/file.bin", "/"), true, 'scan_root "/" must contain deeper descendants');
  assert.strictEqual(cleanupPathWithinRoot("/data2/file.bin", "/data"), false, "prefix collisions must not count as root containment");
  assert.strictEqual(validateCleanupSelection(rootScopedSnapshot, { path: "/", kind: "directory" }).reason.code, "root_path", '"/" itself must stay rejected even when scan_root is "/"');
  assert.strictEqual(validateCleanupSelection(rootScopedSnapshot, { path: "/home", kind: "directory" }).reason.code, "top_level_root", "one-segment targets must stay rejected under scan_root '/'");
  const rootAccepted = validateCleanupSelection(rootScopedSnapshot, { path: "/home/user/file.bin", kind: "file" });
  assert.strictEqual(rootAccepted.accepted, true, "deep descendants must be accepted under complete scan_root '/'");
  assert.strictEqual(rootAccepted.scanRoot, "/", "root-scoped acceptance must stay anchored to '/'");

  const rootFailedSnapshot = {
    server_id: "root-failed",
    selected_roots: [
      { mount_id: "rootfs", mount_root: "/", mountpoint: "/", scan_root: "/", status: "failed" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", scan_root: "/" },
    ],
  };
  assert.strictEqual(validateCleanupSelection(rootFailedSnapshot, { path: "/home/user/file.bin", kind: "file" }).reason.code, "selected_root_status", "deep descendants under failed '/' root must be rejected");

  const collisionSnapshot = {
    server_id: "prefix-collision",
    selected_roots: [
      { mount_id: "data", mount_root: "/", mountpoint: "/data", scan_root: "/data", status: "complete" },
    ],
    mounts: [
      { mount_id: "data", path: "/data", scan_root: "/data" },
    ],
  };
  assert.strictEqual(validateCleanupSelection(collisionSnapshot, { path: "/data2/file.bin", kind: "file" }).reason.code, "outside_selected_roots", "/data vs /data2 prefix collisions must remain rejected");

  const nestedFailedSnapshot = {
    server_id: "nested-failed",
    selected_roots: [
      { mount_id: "rootfs", mount_root: "/", mountpoint: "/", scan_root: "/", status: "complete" },
      { mount_id: "home-user", mount_root: "/", mountpoint: "/home/user", scan_root: "/home/user", status: "failed" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", scan_root: "/" },
      { mount_id: "home-user", path: "/home/user", scan_root: "/home/user" },
    ],
  };
  const longest = cleanupLongestMatchingRoot(nestedFailedSnapshot.selected_roots, "/home/user/file.bin");
  assert.strictEqual(longest && longest.scan_root, "/home/user", "nested root matching must prefer the longest scan_root");
  assert.strictEqual(validateCleanupSelection(nestedFailedSnapshot, { path: "/home/user/file.bin", kind: "file" }).reason.code, "selected_root_status", "a nested failed root must win over an outer complete '/' root");
}
test('testCleanupCommandSafetyContracts', testCleanupCommandSafetyContracts);
