#!/usr/bin/env node
/**
 * Test runner.
 *
 * Two jobs beyond invoking `node --test`:
 *
 *  1. It accepts `--grep <pattern>` and translates it to `--test-name-pattern`, because the brief's
 *     evidence block calls `npm run test -- --grep "cross-tenant|403"` and an evidence command that
 *     does not run is worse than no evidence command.
 *  2. It states LOUDLY whether the live suite ran. Tests that need the running stack are gated on
 *     PORTAL_E2E=1, and a gated-out suite is announced in the output rather than quietly counted as
 *     a pass. A green run that never reached the database is the exact defect pattern this build
 *     keeps producing, so the runner refuses to let it look like a full pass.
 */
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const testDir = join(root, "tests");

const argv = process.argv.slice(2);
const args = [];
for (let index = 0; index < argv.length; index += 1) {
  const arg = argv[index];
  if (arg === "--grep" || arg === "-g") {
    const pattern = argv[index + 1];
    index += 1;
    if (pattern !== undefined) args.push("--test-name-pattern=" + pattern);
    continue;
  }
  if (arg.startsWith("--grep=")) {
    args.push("--test-name-pattern=" + arg.slice("--grep=".length));
    continue;
  }
  args.push(arg);
}

const major = Number.parseInt(process.versions.node.split(".")[0] ?? "0", 10);
const nodeArgs = ["--test"];
// The tests are .ts inside a CommonJS-by-default package, which Node warns about on every file.
// The warning is about a re-parse cost, not about correctness, and eight copies of it in the
// evidence output hides the results. Suppressed by name rather than with a blanket --no-warnings,
// so any other warning still shows.
nodeArgs.push("--disable-warning=MODULE_TYPELESS_PACKAGE_JSON");
// Tests are TypeScript and import the application's own modules rather than a copy of them.
// Node strips types natively from 23.6; 22 needs the flag.
if (major < 23) nodeArgs.push("--experimental-strip-types", "--no-warnings");
nodeArgs.push(...args);

const files = readdirSync(testDir)
  .filter((name) => name.endsWith(".test.ts"))
  .map((name) => join(testDir, name));
nodeArgs.push(...files);

const live = process.env.PORTAL_E2E === "1";
console.log("node " + process.versions.node + " | live suite: " + (live ? "ENABLED" : "DISABLED"));
if (!live) {
  console.log(
    "  ! Tests that need the running stack were NOT RUN. They are gated on PORTAL_E2E=1 and\n" +
      "  ! prove nothing when the stack is down. Set PORTAL_E2E=1 with the portal on\n" +
      "  ! http://127.0.0.1:33000 and BCT_DEV_PASSWORD exported to run them.",
  );
}

const result = spawnSync(process.execPath, nodeArgs, { stdio: "inherit", cwd: root });
process.exit(result.status ?? 1);
