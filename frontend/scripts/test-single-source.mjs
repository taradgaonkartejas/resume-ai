/**
 * Pins the single-source-of-truth invariant for resume editing.
 *
 * The bug this guards against: the guided editor kept its own `draft` copy of
 * structured_data in useState and re-seeded it ONLY when the resume id
 * changed. Accepting an AI suggestion invalidated keys.resume(id), so the
 * live preview updated and the editor did not. Because the autosave PUTs the
 * WHOLE document, the next keystroke then wrote the stale copy back and
 * silently reverted the accepted suggestion.
 *
 * Both symptoms came from the same thing: two mutable copies of one document,
 * one of them write-back. The rule is therefore structural, not cosmetic —
 * there must be exactly ONE copy, the React Query entry keys.resume(id), and
 * the editor must write through to it rather than shadow it.
 *
 * HONEST LIMIT: this is static analysis, not a behavioural test. There is no
 * React test runner in this project, so it cannot prove the hook re-renders
 * correctly — it can only prove the shape that made the bug possible has not
 * come back. It is written to FAIL if someone reintroduces a local document
 * copy or drops one of the two clobber guards.
 *
 * Run: node scripts/test-single-source.mjs
 */
import { readFileSync } from "node:fs";

let failures = 0;
const check = (label, ok, detail = "") => {
  if (ok) {
    console.log(`  ok   ${label}`);
  } else {
    failures += 1;
    console.error(`  FAIL ${label}${detail ? `\n       ${detail}` : ""}`);
  }
};

const autosave = readFileSync("src/features/analysis/useAutosave.ts", "utf8");
const stepper = readFileSync("src/features/analysis/AnalysisStepper.tsx", "utf8");
const workspace = readFileSync("src/features/workspace/Workspace.tsx", "utf8");

// Strip block comments so the prose explaining the old bug cannot satisfy a
// check that is meant to inspect code. (An earlier guard in this repo matched
// its own warning text; see ENV-RECOMMENDED.md.)
const code = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const autosaveCode = code(autosave);

console.log("useAutosave holds no second copy of the document");
check(
  "no useState of StructuredData",
  !/useState<\s*StructuredData/.test(autosaveCode),
  "a local draft is exactly what desynced the editor from the preview",
);
check(
  "no setDraft",
  !/setDraft/.test(autosaveCode),
  "the document is the cache entry; nothing should assign a local copy",
);
check(
  "the id-only re-seed guard is gone",
  !/syncedId/.test(autosaveCode),
  "`if (syncedId.current === resumeId) return;` is the original defect",
);
check(
  "returns the server/cache object as `draft`",
  /draft:\s*serverData/.test(autosaveCode),
  "call sites must render the cache entry, not a shadow copy",
);

console.log("\nwrites go through the cache, with both clobber guards");
check(
  "writes through with setQueryData on keys.resume",
  /setQueryData<\s*ResumeOut\s*>\(\s*keys\.resume\(/.test(autosaveCode),
  "the editor must update the same entry the preview reads",
);
check(
  "cancels an in-flight GET before writing",
  /cancelQueries\(\s*\{\s*queryKey:\s*keys\.resume\(/.test(autosaveCode),
  "a GET already on the wire would land after the keystroke and revert it",
);
check(
  "re-applies pending edits after a save",
  /if\s*\(\s*pending\.current\s*\)\s*writeCache\(\s*pending\.current\s*\)/.test(
    autosaveCode,
  ),
  "the PUT echo is the pre-keystroke document when the user typed during it",
);

console.log("\nboth panes read the one entry");
check(
  "editor is fed resume.structured_data",
  /useAutosave\(\s*resumeId,\s*resume\?\.structured_data/.test(code(stepper)),
  "AnalysisStepper must source the editor from the cached resume",
);
check(
  "preview is fed resume.structured_data",
  /const\s+data\s*=\s*resume\?\.structured_data/.test(code(workspace)),
  "Workspace must not introduce a parallel copy for the preview",
);

console.log(
  failures === 0
    ? "\nsingle-source invariant holds"
    : `\n${failures} check(s) failed`,
);
process.exit(failures === 0 ? 0 : 1);
