/**
 * Every `reason` the backend can return must be named in client.ts.
 *
 * Why this exists. The comment above `isFinalRefusal` already warned:
 *
 *   "a new reason that matched no branch would quietly fall back to the
 *    retryable path and offer a button that can never succeed"
 *
 * `critic_rejected` was then added to the backend and did exactly that. The
 * warning was correct and completely ineffective, because a comment is not a
 * guard. This is the guard.
 *
 * It reads the OTHER language's source rather than a copy of the list --
 * duplicating the reasons here would just move the drift somewhere quieter.
 *
 * Run: node scripts/test-error-reasons.mjs
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

let failures = 0;
function ok(label, cond, detail = "") {
  if (cond) {
    console.log(`  ok   ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${label}${detail ? ` -- ${detail}` : ""}`);
  }
}

const BACKEND = "../backend/app";
const CLIENT = "src/api/client.ts";

/** Every `reason="..."` literal the backend raises, found by walking app/. */
function backendReasons(dir) {
  const found = new Set();
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__pycache__") continue;
      for (const r of backendReasons(full)) found.add(r);
    } else if (entry.name.endsWith(".py")) {
      const src = readFileSync(full, "utf8");
      for (const m of src.matchAll(/reason\s*=\s*"([a-z_]+)"/g)) found.add(m[1]);
    }
  }
  return found;
}

console.log("error reasons: backend -> client.ts");

const reasons = backendReasons(BACKEND);
const client = readFileSync(CLIENT, "utf8");

ok(
  "found the backend reason literals at all",
  reasons.size >= 3,
  `only found ${[...reasons].join(", ") || "none"} -- has the pattern changed?`,
);

for (const reason of [...reasons].sort()) {
  ok(
    `client.ts names "${reason}"`,
    client.includes(`"${reason}"`),
    "it would fall through to the generic retryable path",
  );
}

/* The two states that must stay distinct. Collapsing them is the specific
   mistake this file is here to prevent: `critic_rejected` is retryable
   (the writer is stochastic) while `needs_your_fact` is not (the model has
   reasoned that the number is unknowable and will do so again). */
const finalRefusal = client.slice(
  client.indexOf("get isFinalRefusal"),
  client.indexOf("get isReviewerObjection"),
);
ok(
  "critic_rejected is NOT lumped into isFinalRefusal",
  !finalRefusal.includes("critic_rejected"),
  "a retryable objection would stop offering the retry that can fix it",
);
ok(
  "needs_your_fact IS a final refusal",
  finalRefusal.includes("needs_your_fact"),
);
ok(
  "writer_not_configured IS a final refusal",
  finalRefusal.includes("writer_not_configured"),
);

console.log(
  failures === 0
    ? `\n>>> all ${reasons.size + 4} checks passed`
    : `\n>>> ${failures} FAILED`,
);
process.exit(failures === 0 ? 0 : 1);
