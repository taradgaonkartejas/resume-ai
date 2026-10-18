/**
 * Unit tests for src/features/analysis/groupFindings.ts.
 *
 * Run:  node scripts/test-group-findings.mjs      (from frontend/)
 *
 * The repo has no frontend test runner yet, and adding vitest is a dependency
 * decision that is not mine to make silently -- so this script is
 * self-contained: it transpiles the module under test with the tsc that is
 * already installed, stubs its one runtime import, and asserts. If a runner
 * is adopted later, the cases below port over unchanged.
 *
 * `rankFindings` is stubbed to identity. It belongs to analysisService and has
 * its own meaning; what is graded here is partitioning and labelling, so the
 * one order-sensitive assertion sorts before comparing.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "groupfindings-"));
try {
  const src = readFileSync("src/features/analysis/groupFindings.ts", "utf8");
  const needle =
    'import { rankFindings, type Finding, type StructuredData } from "@/services";';
  if (!src.includes(needle)) {
    console.error(
      "FATAL: the import line this harness rewrites has changed.\n" +
        "       Update scripts/test-group-findings.mjs to match, do not delete the check.",
    );
    process.exit(2);
  }
  writeFileSync(
    join(dir, "groupFindings.ts"),
    src.replace(
      needle,
      'import { rankFindings } from "./rank";\ntype Finding = any;\ntype StructuredData = any;',
    ),
  );
  writeFileSync(
    join(dir, "rank.ts"),
    "export function rankFindings<T>(f: T[]): T[] { return f; }\n",
  );
  execFileSync(
    "./node_modules/.bin/tsc",
    [
      join(dir, "groupFindings.ts"),
      join(dir, "rank.ts"),
      "--module", "esnext",
      "--target", "es2022",
      "--moduleResolution", "bundler",
    ],
    { stdio: "inherit" },
  );
  const out = join(dir, "groupFindings.mjs");
  writeFileSync(
    out,
    readFileSync(join(dir, "groupFindings.js"), "utf8").replace(
      'from "./rank"',
      'from "./rank.mjs"',
    ),
  );
  writeFileSync(
    join(dir, "rank.mjs"),
    readFileSync(join(dir, "rank.js"), "utf8"),
  );

  const { roleIndexOf, groupFindingsByRole } = await import(`file://${out}`);

  let pass = 0;
  let fail = 0;
  const eq = (name, got, want) => {
    const g = JSON.stringify(got);
    const w = JSON.stringify(want);
    if (g === w) {
      pass++;
      console.log("  ok  " + name);
    } else {
      fail++;
      console.log(`FAIL  ${name}\n      got  ${g}\n      want ${w}`);
    }
  };
  const f = (id, target_ref) => ({
    id,
    target_ref,
    points: 1,
    severity: "low",
    message: id,
    fix_hint: "",
    action: "",
  });

  eq("roleIndexOf exp_2.bullet_1", roleIndexOf("exp_2.bullet_1"), 2);
  eq("roleIndexOf exp_0", roleIndexOf("exp_0"), 0);
  eq("roleIndexOf exp_10.x", roleIndexOf("exp_10.x"), 10);
  eq("roleIndexOf summary", roleIndexOf("summary"), null);
  eq("roleIndexOf contact.phone", roleIndexOf("contact.phone"), null);
  // The \b is what stops these three being read as role 1 / role 0.
  eq("roleIndexOf exp_abc", roleIndexOf("exp_abc"), null);
  eq("roleIndexOf experience", roleIndexOf("experience"), null);
  eq("roleIndexOf exp_1x", roleIndexOf("exp_1x"), null);

  const data = {
    experience: [
      { role: "Engineer", company: "Acme", dates: "2020-2022" },
      { role: "", company: "Globex", dates: "" },
      { role: "Analyst", company: "", dates: "2018" },
    ],
  };

  const r1 = groupFindingsByRole(
    [
      f("a", "exp_1.bullets"),
      f("b", "exp_0.bullets"),
      f("c", "summary"),
      f("d", "exp_0.title"),
    ],
    data,
  );
  eq("groups sorted by role index", r1.groups.map((g) => g.key), [
    "exp_0",
    "exp_1",
  ]);
  eq("non-role finding ungrouped", r1.ungrouped.map((x) => x.id), ["c"]);
  eq("same role collected", r1.groups[0].findings.map((x) => x.id).sort(), [
    "b",
    "d",
  ]);
  eq("title role at company", r1.groups[0].title, "Engineer at Acme");
  eq("subtitle does not repeat company", r1.groups[0].subtitle, "2020-2022");
  eq("company-only title", r1.groups[1].title, "Globex");

  // A ref pointing past the end of the array: the user deleted a job after
  // the analysis ran. Must not invent a heading, must not drop the finding.
  const r2 = groupFindingsByRole([f("x", "exp_7.bullets")], data);
  eq("out-of-range ref -> ungrouped", r2.ungrouped.map((x) => x.id), ["x"]);
  eq("out-of-range invents no group", r2.groups.length, 0);

  const r3 = groupFindingsByRole([f("y", "exp_0.b")], undefined);
  eq("no data -> everything ungrouped", r3.ungrouped.map((x) => x.id), ["y"]);
  eq("no data -> no groups", r3.groups.length, 0);

  eq("empty input", groupFindingsByRole([], data), {
    groups: [],
    ungrouped: [],
  });

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
} finally {
  rmSync(dir, { recursive: true, force: true });
}
