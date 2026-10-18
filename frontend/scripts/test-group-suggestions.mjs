/**
 * Unit tests for src/features/tailor/groupSuggestions.ts.
 *
 * Run:  node scripts/test-group-suggestions.mjs      (from frontend/)
 *
 * Same arrangement as test-group-findings.mjs: no frontend test runner
 * exists yet, so this transpiles the module with the installed tsc and
 * asserts against it. The module's only import is a type, which erases at
 * compile time, so nothing needs stubbing here.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "groupsuggestions-"));
try {
  const src = readFileSync("src/features/tailor/groupSuggestions.ts", "utf8");
  const needle = 'import type { SuggestionOut } from "@/services";';
  if (!src.includes(needle)) {
    console.error(
      "FATAL: the import line this harness rewrites has changed.\n" +
        "       Update scripts/test-group-suggestions.mjs to match, do not delete the check.",
    );
    process.exit(2);
  }
  writeFileSync(
    join(dir, "groupSuggestions.ts"),
    src.replace(needle, "type SuggestionOut = any;"),
  );
  execFileSync(
    "./node_modules/.bin/tsc",
    [
      join(dir, "groupSuggestions.ts"),
      "--module", "esnext",
      "--target", "es2022",
      "--moduleResolution", "bundler",
    ],
    { stdio: "inherit" },
  );
  const out = join(dir, "groupSuggestions.mjs");
  writeFileSync(out, readFileSync(join(dir, "groupSuggestions.js"), "utf8"));

  const { sectionOfRef, sectionOf, groupSuggestions } = await import(`file://${out}`);

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
  const s = (id, target_ref, section = "", keywords = []) => ({
    id,
    target_ref,
    section,
    keywords,
    status: "pending",
  });

  /* ------------------------------------------------ ref -> section ---- */
  eq("ref summary.text", sectionOfRef("summary.text"), "summary");
  eq("ref exp_0.bullet_2", sectionOfRef("exp_0.bullet_2"), "experience");
  eq("ref prj_1.bullet_0", sectionOfRef("prj_1.bullet_0"), "projects");
  eq("ref skills.0.items", sectionOfRef("skills.0.items"), "skills");
  eq("ref edu_0.bullet_0", sectionOfRef("edu_0.bullet_0"), "education");
  eq("unknown ref falls to other", sectionOfRef("contact.email"), "other");
  eq("empty ref falls to other", sectionOfRef(""), "other");

  /* ------------------------------------- server label wins over ref ---- */
  eq(
    "section field is trusted",
    sectionOf({ section: "projects", target_ref: "exp_0.bullet_0" }),
    "projects",
  );
  eq(
    "blank section falls back to the ref",
    sectionOf({ section: "", target_ref: "exp_3.bullet_1" }),
    "experience",
  );
  eq(
    "unrecognised section falls back to the ref",
    sectionOf({ section: "banana", target_ref: "summary.text" }),
    "summary",
  );
  eq(
    "section is matched case-insensitively",
    sectionOf({ section: "Experience", target_ref: "summary.text" }),
    "experience",
  );
  // Neither source is usable: still a group, never a dropped card.
  eq(
    "both unusable still yields a bucket",
    sectionOf({ section: "", target_ref: "weird" }),
    "other",
  );

  /* ---------------------------------------------------- grouping ------ */
  const r1 = groupSuggestions([
    s("a", "exp_0.bullet_0", "experience"),
    s("b", "summary.text", "summary"),
    s("c", "exp_1.bullet_0", "experience"),
    s("d", "skills.0.items", "skills"),
  ]);
  eq(
    "groups follow resume order, not arrival order",
    r1.map((g) => g.key),
    ["summary", "experience", "skills"],
  );
  eq("group counts", r1.map((g) => g.suggestions.length), [1, 2, 1]);
  eq(
    "server order is preserved inside a group",
    r1.find((g) => g.key === "experience").suggestions.map((x) => x.id),
    ["a", "c"],
  );
  eq("titles are human", r1.map((g) => g.title), [
    "Professional summary",
    "Experience",
    "Skills",
  ]);

  // An empty section must not render as an empty heading.
  eq(
    "empty sections are omitted",
    groupSuggestions([s("a", "summary.text", "summary")]).map((g) => g.key),
    ["summary"],
  );
  eq("empty input yields no groups", groupSuggestions([]), []);

  /* ---------------------------------------------------- keywords ------ */
  const r2 = groupSuggestions([
    s("a", "exp_0.bullet_0", "experience", ["Kubernetes", "Terraform"]),
    s("b", "exp_1.bullet_0", "experience", ["terraform", "AWS"]),
  ]);
  eq(
    "keywords de-duplicate case-insensitively, first spelling wins",
    r2[0].keywords,
    ["Kubernetes", "Terraform", "AWS"],
  );
  const r3 = groupSuggestions([
    s("a", "exp_0.bullet_0", "experience", ["", "  ", "Redis"]),
  ]);
  eq("blank keywords are dropped", r3[0].keywords, ["Redis"]);
  const r4 = groupSuggestions([s("a", "exp_0.bullet_0", "experience")]);
  eq("missing keywords is an empty list, not a crash", r4[0].keywords, []);

  /* ------------------------------------------------ nothing is lost --- */
  const many = [
    s("a", "summary.text", "summary"),
    s("b", "exp_0.bullet_0", "experience"),
    s("c", "prj_0.bullet_0", "projects"),
    s("d", "skills.0.items", "skills"),
    s("e", "contact.email", ""),
  ];
  eq(
    "every suggestion lands in exactly one group",
    groupSuggestions(many).reduce((n, g) => n + g.suggestions.length, 0),
    many.length,
  );
  eq(
    "an unplaceable card is surfaced, not dropped",
    groupSuggestions(many).find((g) => g.key === "other").suggestions.map((x) => x.id),
    ["e"],
  );

  console.log(fail ? `\n>>> ${fail} FAILURES (${pass} passed)` : `\n>>> all ${pass} checks passed`);
  process.exit(fail ? 1 : 0);
} finally {
  rmSync(dir, { recursive: true, force: true });
}
