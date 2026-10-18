/**
 * Pins the target-ref vocabulary the preview highlight depends on.
 *
 * These strings must match `backend/app/services/resume_ops.py` exactly. The
 * seeded demo data once shipped `experience.0.bullets.0`, which the resolver
 * rejects with InvalidTargetRef -- it type-checked, it round-tripped through
 * the schema, and it was simply never compared against the resolver. A
 * mismatch here fails the same silent way: the highlight just never shows.
 *
 * Run: node scripts/test-change-refs.mjs
 */
import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  copyFileSync,
  readFileSync,
  writeFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Same arrangement as test-group-suggestions.mjs: no frontend test runner
// exists yet, so this transpiles with the installed tsc. changeRefs.ts has
// no imports at all, so nothing needs stubbing.
const dir = mkdtempSync(join(tmpdir(), "changerefs-"));
copyFileSync("src/features/preview/changeRefs.ts", join(dir, "changeRefs.ts"));
execFileSync(
  "./node_modules/.bin/tsc",
  [
    join(dir, "changeRefs.ts"),
    "--module", "esnext",
    "--target", "es2022",
    "--moduleResolution", "bundler",
  ],
  { stdio: "inherit" },
);
writeFileSync(join(dir, "package.json"), '{"type":"module"}');
const m = await import(join(dir, "changeRefs.js"));

let pass = 0;
const failures = [];
function check(name, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a === e) pass++;
  else failures.push(`${name}\n    expected ${e}\n    actual   ${a}`);
}
function ok(name, cond) {
  if (cond) pass++;
  else failures.push(name);
}

// ---------------------------------------------------------------- spelling
// The exact four forms resume_ops.resolve() accepts.
check("summary ref", m.SUMMARY_REF, "summary.text");
check("experience bullet 0,0", m.experienceBulletRef(0, 0), "exp_0.bullet_0");
check("experience bullet 2,5", m.experienceBulletRef(2, 5), "exp_2.bullet_5");
check("project bullet 0,0", m.projectBulletRef(0, 0), "prj_0.bullet_0");
check("project bullet 1,3", m.projectBulletRef(1, 3), "prj_1.bullet_3");
check("skills group 0", m.skillsRef(0), "skills.0.items");
check("skills group 2", m.skillsRef(2), "skills.2.items");

// The long dotted form is the one that raises InvalidTargetRef. If anyone
// "fixes" the builders to look more like the JSON shape, this catches it.
ok(
  "experience ref is NOT the long dotted form",
  m.experienceBulletRef(0, 0) !== "experience.0.bullets.0",
);
ok(
  "project ref is NOT the long dotted form",
  m.projectBulletRef(0, 0) !== "projects.0.bullets.0",
);

// ----------------------------------------------------------- education
check("education school", m.educationRef(0, "school"), "edu_0.school");
check("education degree", m.educationRef(1, "degree"), "edu_1.degree");
check("education field_of_study", m.educationRef(0, "field_of_study"), "edu_0.field_of_study");

// The field list is spelled in two languages; resume_ops._EDU_FIELDS is the
// source of truth. A field here that the resolver rejects would raise
// InvalidTargetRef at accept time -- the same failure the seeded demo data
// shipped with.
{
  const py = readFileSync("../backend/app/services/resume_ops.py", "utf8");
  const m2 = py.match(/_EDU_FIELDS\s*=\s*\(([^)]*)\)/s);
  ok("backend defines _EDU_FIELDS", Boolean(m2));
  if (m2) {
    const backend = [...m2[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
    check("education fields match the backend allowlist", [...m.EDUCATION_FIELDS], backend);
    ok("dates is not addressable", !backend.includes("dates"));
  }
}

// ------------------------------------------------------------- set / toggle
const changed = m.changedRefSet(["exp_0.bullet_0", "summary.text"]);
ok("set contains a marked ref", changed.has("exp_0.bullet_0"));
ok("set omits an unmarked ref", !changed.has("exp_0.bullet_1"));
check("empty list yields empty set", m.changedRefSet([]).size, 0);
check("duplicates collapse", m.changedRefSet(["a", "a", "b"]).size, 2);

ok("isChanged true when shown and present", m.isChanged("summary.text", changed, true));
ok("isChanged false when present but hidden", !m.isChanged("summary.text", changed, false));
ok("isChanged false when shown but absent", !m.isChanged("prj_0.bullet_0", changed, true));
ok("isChanged false when hidden and absent", !m.isChanged("nope", changed, false));

// The toggle must win over membership -- a highlight that survives "Show
// Changes: off" is worse than no highlight.
ok(
  "toggle off suppresses every ref",
  ["exp_0.bullet_0", "summary.text"].every((r) => !m.isChanged(r, changed, false)),
);

// ------------------------------------------- cross-language: the sentinel
// The placeholder literal is spelled in TWO languages. Nothing at build time
// compares them, so this does: read the Python source of truth and the TS
// mirror and assert they agree. A silent divergence would mean the export
// guard looks for one string while the writer emits another -- i.e. a resume
// full of blanks sails straight past the check meant to stop it.
{
  const py = readFileSync("../backend/app/services/placeholders.py", "utf8");
  const m = py.match(/^PLACEHOLDER\s*=\s*"([^"]+)"/m);
  ok("backend defines PLACEHOLDER", Boolean(m));

  const ts = readFileSync("src/lib/placeholder.ts", "utf8");
  const t = ts.match(/METRIC_PLACEHOLDER\s*=\s*"([^"]+)"/);
  ok("frontend defines METRIC_PLACEHOLDER", Boolean(t));

  if (m && t) {
    check("frontend placeholder matches the backend", t[1], m[1]);
  }
}

rmSync(dir, { recursive: true, force: true });

for (const f of failures) console.error("FAIL " + f);
console.log(
  failures.length
    ? `>>> ${pass} passed, ${failures.length} FAILED`
    : `>>> all ${pass} checks passed`,
);
process.exit(failures.length ? 1 : 0);
