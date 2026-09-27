/**
 * Date-label parity: the browser's dateLabel() must agree with the server's
 * resume_ops.date_label() for every input, always.
 *
 * Why this exists. The preview renders the user's *unsaved* draft, so it
 * cannot ask the server what a date range looks like mid-keystroke — the rule
 * is implemented twice, once in Python and once in TypeScript. Two copies of
 * a rule drift. This script is the thing that stops them: it transpiles the
 * REAL src/lib/dates.ts (no reimplementation here) and compares it against the
 * REAL backend by round-tripping documents through PUT /resumes/{id}/data and
 * reading back the derived `dates` mirror the server computed.
 *
 *   node scripts/verify-date-parity.mjs
 *
 * Requires a server on :8000 (real backend or mock-api.py — must pass both).
 */

import { rolldown } from "rolldown";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import axios from "axios";

const BASE = process.env.API_BASE ?? "http://127.0.0.1:8000/api";

let pass = 0;
let fail = 0;
function ok(cond, label, detail = "") {
  if (cond) {
    pass++;
    console.log(`  OK   ${label}${detail ? ` -- ${detail}` : ""}`);
  } else {
    fail++;
    console.log(`  FAIL ${label}${detail ? ` -- ${detail}` : ""}`);
  }
}
function section(name) {
  console.log(`\n=== ${name} ===`);
}

/* ------------------------------------------------- load the real module --- */
// Transpile src/lib/dates.ts rather than copying its logic, so this script
// cannot pass while the shipped implementation is wrong.
const outDir = await mkdtemp(join(tmpdir(), "dateparity-"));
const bundle = await rolldown({
  input: "src/lib/dates.ts",
  // The module only imports types from @/services; stub the alias away.
  external: [/^@\/services$/],
  logLevel: "silent",
});
const { output } = await bundle.generate({ format: "esm" });
const file = join(outDir, "dates.mjs");
await writeFile(file, output[0].code);
const { dateLabel } = await import(pathToFileURL(file).href);
ok(typeof dateLabel === "function", "loaded dateLabel() from src/lib/dates.ts");

/* ---------------------------------------------------------- local table --- */
section("client rules");
const CASES = [
  { entry: { start_date: "Jan 2020", current: true }, want: "Jan 2020 – Present" },
  { entry: { start_date: "Jan 2020", end_date: "Feb 2022" }, want: "Jan 2020 – Feb 2022" },
  { entry: { start_date: "2020" }, want: "2020" },
  { entry: { end_date: "2022" }, want: "2022" },
  { entry: { current: true }, want: "Present" },
  { entry: { raw_dates: "Various dates" }, want: "Various dates" },
  { entry: { dates: "legacy only" }, want: "legacy only" },
  { entry: {}, want: "" },
  // current must win over a retained end date
  { entry: { start_date: "Jan 2020", end_date: "Feb 2022", current: true },
    want: "Jan 2020 – Present" },
  // an ambiguous original beats everything: never reinterpreted
  { entry: { raw_dates: "Summer 2020", start_date: "2020", current: true },
    want: "Summer 2020" },
];
for (const { entry, want } of CASES) {
  const got = dateLabel(entry);
  ok(got === want, `dateLabel(${JSON.stringify(entry)})`, `${JSON.stringify(got)}`);
  if (got !== want) console.log(`       expected ${JSON.stringify(want)}`);
}

ok(dateLabel(null) === "", "dateLabel(null) is empty, not a crash");
ok(dateLabel(undefined) === "", "dateLabel(undefined) is empty, not a crash");

/* --------------------------------------------------------- server parity --- */
section("server parity");
const users = (await axios.get(`${BASE}/users`)).data;
const api = axios.create({
  baseURL: BASE,
  headers: { "X-User-Id": users[0].id },
  timeout: 15000,
});

const created = (await api.post("/resumes", { title: "date-parity probe" })).data;
const RID = created.id;

try {
  const EXPERIMENTS = [
    { start_date: "Jan 2020", end_date: "", current: true },
    { start_date: "Jan 2020", end_date: "Feb 2022", current: false },
    { start_date: "2019", end_date: "2022", current: false },
    { start_date: "", end_date: "", current: true },
    { start_date: "Mar 2021", end_date: "Dec 2023", current: true },
    { start_date: "", end_date: "", current: false },
    { start_date: "Sept 2018", end_date: "", current: false },
  ];

  for (const fields of EXPERIMENTS) {
    const payload = {
      contact: { name: "P", headline: "", email: "", phone: "", location: "", links: [] },
      summary: { text: "" },
      experience: [{ company: "Acme", role: "SRE", bullets: [], ...fields }],
      projects: [],
      education: [],
      skills: [],
    };
    const saved = (await api.put(`/resumes/${RID}/data`, { structured_data: payload })).data;
    const entry = saved.structured_data.experience[0];
    const client = dateLabel(entry);
    ok(
      entry.dates === client,
      `server mirror === client label for ${JSON.stringify(fields)}`,
      `server=${JSON.stringify(entry.dates)} client=${JSON.stringify(client)}`,
    );
  }

  // Legacy free-text must round-trip through the server's migration and still
  // agree — this is the path every pre-existing stored resume takes.
  section("legacy migration parity");
  const LEGACY = [
    ["Jan 2020 – Present", true],
    ["2019 - 2021", true],
    ["March 2018 to June 2021", true],
    ["Various dates", false],   // ambiguous: preserved verbatim
    ["Summer 2020", false],
    ["2020", false],
  ];
  for (const [raw, splittable] of LEGACY) {
    const payload = {
      contact: { name: "P", headline: "", email: "", phone: "", location: "", links: [] },
      summary: { text: "" },
      experience: [{ company: "Acme", role: "SRE", dates: raw, bullets: [] }],
      projects: [],
      education: [],
      skills: [],
    };
    const saved = (await api.put(`/resumes/${RID}/data`, { structured_data: payload })).data;
    const entry = saved.structured_data.experience[0];
    ok(
      entry.dates === dateLabel(entry),
      `legacy ${JSON.stringify(raw)} agrees after migration`,
      `${JSON.stringify(entry.dates)}`,
    );
    if (splittable) {
      ok(!entry.raw_dates, `  ${JSON.stringify(raw)} was split into fields`,
        `start=${JSON.stringify(entry.start_date)}`);
    } else {
      ok(entry.raw_dates === raw,
        `  ${JSON.stringify(raw)} preserved verbatim rather than guessed`,
        `raw_dates=${JSON.stringify(entry.raw_dates)}`);
    }
  }

  // Idempotence: saving what the server returned must not change anything.
  section("idempotence");
  const once = (await api.get(`/resumes/${RID}`)).data.structured_data;
  const twice = (await api.put(`/resumes/${RID}/data`, { structured_data: once }))
    .data.structured_data;
  ok(
    JSON.stringify(once) === JSON.stringify(twice),
    "re-saving a server document is a no-op (migration is idempotent)",
  );
} finally {
  await api.delete(`/resumes/${RID}`).catch(() => {});
  await rm(outDir, { recursive: true, force: true });
}

console.log(
  fail === 0
    ? `\n>>> all ${pass} checks passed`
    : `\n>>> ${fail} FAILED, ${pass} passed`,
);
process.exit(fail === 0 ? 0 : 1);
