/**
 * Unit tests for src/features/analysis/formatFixes.ts.
 *
 * Run:  node scripts/test-format-fixes.mjs      (from frontend/)
 *
 * Same self-contained approach as test-group-findings.mjs: no runner is
 * installed, so this transpiles the module with the local tsc and asserts.
 *
 * What is actually at stake here is ORDER and FAILURE TOLERANCE -- a bulk
 * action that stops at the first refusal, or that drafts before it reorders,
 * is broken in a way the UI would not obviously show.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const dir = mkdtempSync(join(tmpdir(), "formatfixes-"));
try {
  const src = readFileSync("src/features/analysis/formatFixes.ts", "utf8");
  const needle = 'import type { Finding, SuggestionOut } from "@/services";';
  if (!src.includes(needle)) {
    console.error(
      "FATAL: the import line this harness rewrites has changed.\n" +
        "       Update scripts/test-format-fixes.mjs to match.",
    );
    process.exit(2);
  }
  writeFileSync(
    join(dir, "formatFixes.ts"),
    src.replace(needle, "type Finding = any;\ntype SuggestionOut = any;"),
  );
  execFileSync(
    "./node_modules/.bin/tsc",
    [join(dir, "formatFixes.ts"), "--module", "esnext", "--target", "es2022",
     "--moduleResolution", "bundler"],
    { stdio: "inherit" },
  );
  const out = join(dir, "formatFixes.mjs");
  writeFileSync(out, readFileSync(join(dir, "formatFixes.js"), "utf8"));
  const { runFormatFixes, partitionFormatFindings } = await import(`file://${out}`);

  let pass = 0, fail = 0;
  const eq = (name, got, want) => {
    const g = JSON.stringify(got), w = JSON.stringify(want);
    if (g === w) { pass++; console.log("  ok  " + name); }
    else { fail++; console.log(`FAIL  ${name}\n      got  ${g}\n      want ${w}`); }
  };
  const f = (id, action, target_ref = id) => ({
    id, action, target_ref, message: `msg:${id}`,
    points: 1, severity: "low", fix_hint: "",
  });

  const FINDINGS = [
    f("format.no_skills", ""),
    f("format.overlong_bullets", "rewrite", "exp_0.bullet_0"),
    f("ats.not_reverse_chronological", "rerank", "experience"),
    f("ats.decorative_glyphs", ""),
  ];

  // --- partition ---
  const p = partitionFormatFindings(FINDINGS);
  eq("auto = rerank only", p.auto.map((x) => x.id), ["ats.not_reverse_chronological"]);
  eq("draftable = rewrite only", p.draftable.map((x) => x.id), ["format.overlong_bullets"]);
  eq("manual = no action", p.manual.map((x) => x.id), ["format.no_skills", "ats.decorative_glyphs"]);
  eq("actionable count", p.actionable, 2);
  eq("empty partitions", partitionFormatFindings([]).actionable, 0);

  // --- ordering: deterministic BEFORE drafts ---
  {
    const calls = [];
    await runFormatFixes({
      findings: FINDINGS,
      rerank: async (ref) => { calls.push(`rerank:${ref}`); },
      rewrite: async ({ targetRef }) => { calls.push(`rewrite:${targetRef}`); return { id: "s1" }; },
    });
    eq("rerank runs before rewrite", calls, [
      "rerank:experience", "rewrite:exp_0.bullet_0",
    ]);
  }

  // --- tallies and draft keying ---
  {
    const r = await runFormatFixes({
      findings: FINDINGS,
      rerank: async () => {},
      rewrite: async () => ({ id: "sug-1" }),
    });
    eq("applied counted", r.applied, 1);
    eq("drafted counted", r.drafted, 1);
    eq("no failures", r.failed, []);
    eq("draft keyed by finding+ref", Object.keys(r.drafts), [
      "format.overlong_bullets::exp_0.bullet_0",
    ]);
  }

  // --- one refusal must not abandon the queue ---
  {
    const seen = [];
    const many = [
      f("ats.not_reverse_chronological", "rerank", "experience"),
      f("a", "rewrite", "exp_0.bullet_0"),
      f("b", "rewrite", "exp_0.bullet_1"),
      f("c", "rewrite", "exp_0.bullet_2"),
    ];
    const r = await runFormatFixes({
      findings: many,
      rerank: async () => { throw new Error("409 suggestions open"); },
      rewrite: async ({ targetRef }) => {
        seen.push(targetRef);
        if (targetRef.endsWith("bullet_1")) throw new Error("needs_your_fact");
        return { id: targetRef };
      },
    });
    eq("every later item still attempted", seen, [
      "exp_0.bullet_0", "exp_0.bullet_1", "exp_0.bullet_2",
    ]);
    eq("successes still counted", r.drafted, 2);
    eq("rerank failure recorded", r.failed.includes("409 suggestions open"), true);
    eq("writer refusal recorded", r.failed.includes("needs_your_fact"), true);
    eq("two failures total", r.failed.length, 2);
    eq("nothing applied", r.applied, 0);
  }

  // --- a thrown non-Error must still produce a usable message ---
  {
    const r = await runFormatFixes({
      findings: [f("x", "rewrite", "exp_0.bullet_0")],
      rerank: async () => {},
      rewrite: async () => { throw { nope: true }; },
    });
    eq("falls back to the finding's message", r.failed, ["Could not fix: msg:x"]);
  }

  // --- progress is reported, including the initial 0 ---
  {
    const ticks = [];
    await runFormatFixes({
      findings: FINDINGS,
      rerank: async () => {},
      rewrite: async () => ({ id: "s" }),
      onProgress: (d, t) => ticks.push(`${d}/${t}`),
    });
    eq("progress ticks", ticks, ["0/2", "1/2", "2/2"]);
  }

  // --- nothing actionable: no calls at all ---
  {
    let touched = 0;
    const r = await runFormatFixes({
      findings: [f("format.no_skills", "")],
      rerank: async () => { touched++; },
      rewrite: async () => { touched++; return {}; },
    });
    eq("manual-only makes no calls", touched, 0);
    eq("manual-only reports nothing", [r.applied, r.drafted, r.failed.length], [0, 0, 0]);
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
} finally {
  rmSync(dir, { recursive: true, force: true });
}
