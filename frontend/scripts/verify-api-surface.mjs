/**
 * Exercise EVERY backend operation through the real axios gateway.
 *
 * verify-gateway.mjs proves the transport (headers, FormData, blobs, errors).
 * This proves the CONTRACT: that each route returns the shape services/types.ts
 * claims, with the field names and status codes the UI actually reads.
 *
 * Run against the real API (not mock-api.py) -- the mock has drifted before,
 * so a green run there proves nothing about production behaviour:
 *
 *   uvicorn app.main:app --port 8000
 *   node scripts/verify-api-surface.mjs
 */
import axios from "axios";

const BASE = process.env.API_BASE ?? "http://localhost:8000/api";

let pass = 0,
  fail = 0;
const results = [];

function ok(cond, label, detail = "") {
  if (cond) {
    pass++;
    console.log(`  OK   ${label}${detail ? ` -- ${detail}` : ""}`);
  } else {
    fail++;
    console.log(`  FAIL ${label}${detail ? ` -- ${detail}` : ""}`);
  }
  return cond;
}

/** Assert an object carries exactly the fields the frontend expects. */
function hasFields(obj, fields, label) {
  const missing = fields.filter((f) => !(f in (obj ?? {})));
  return ok(missing.length === 0, label, missing.length ? `missing ${missing}` : `${fields.length} fields`);
}

async function expectStatus(promise, want, label) {
  try {
    await promise;
    return ok(false, label, "no error thrown");
  } catch (e) {
    const got = e.response?.status ?? 0;
    return ok(got === want, label, `got ${got}`);
  }
}

const section = (t) => console.log(`\n=== ${t} ===`);

/**
 * Record a section that could not run, loudly and in the tally.
 *
 * Not counted as a pass: a skipped section has proved nothing, and a gate
 * that reports "all green" while silently skipping is worse than one that
 * fails.
 */
let skipped = 0;
function skip(why) {
  skipped++;
  console.log(`  SKIP ${why}`);
}

const users = (await axios.get(`${BASE}/users`)).data;
const USER = users[0].id;
const OTHER = users[1].id;
const api = axios.create({ baseURL: BASE, headers: { "X-User-Id": USER } });
const asOther = axios.create({ baseURL: BASE, headers: { "X-User-Id": OTHER } });

/* ------------------------------------------------------------ system ---- */
section("system");
const health = (await api.get("/health")).data;
hasFields(health, ["status", "database", "pgvector", "storage", "llm_configured", "model", "users"], "GET /health");
// The AI-rewrite section needs a writer. Without one the deterministic
// fallback returns the bullet unchanged, which the service reports as a
// 422 needs_your_fact -- a legitimate answer that used to crash this
// script and take the two sections after it down with it.
const LLM_ON = health.llm_configured === true;
hasFields(users[0], ["id", "name", "title", "avatar_color", "chat_tokens_left"], "GET /users");
ok(users.length === 5, "5 seeded users", `${users.length}`);

const templates = (await api.get("/templates")).data;
hasFields(templates[0], ["key", "name", "description", "design_tokens"], "GET /templates");
ok(templates.length === 7, "7 templates", `${templates.length}`);
const tokenKeys = ["font", "heading", "name_align", "accent", "density", "caps", "divider", "header", "entry", "skill_columns"];
hasFields(templates[0].design_tokens, tokenKeys, "design_tokens 10-token contract");

const jd = (await api.get("/sample-jd")).data;
hasFields(jd, ["id", "title", "content", "extracted_keywords"], "GET /sample-jd");

const runs = (await api.get("/admin/agent-runs")).data;
ok(Array.isArray(runs), "GET /admin/agent-runs is a list", `${runs.length} rows`);

/* ----------------------------------------------------------- resumes ---- */
section("resumes");
const list = (await api.get("/resumes")).data;
hasFields(list[0], ["id", "title", "kind", "parent_id", "tailored_for", "template_key", "parse_status", "overall_score", "structured_data", "updated_at"], "GET /resumes card shape");
ok(list[0].overall_score === null || typeof list[0].overall_score === "number", "overall_score null|number", `${list[0].overall_score}`);

// This probe DELETES a base at the end, so it uploads its own resume rather
// than destroying seed data -- otherwise a second run operates on wreckage
// from the first. Upload (not POST /resumes) so we get real parsed content
// for the analysis and tailoring stages.
// The fixture is defined HERE, not copied from list[0]. Sourcing it from an
// unowned row made the probe depend on which resume happened to sort first:
// once an earlier run left a tailored fork behind (1 bullet), the suggestion
// stage silently had too little content and reported a false failure. A probe
// must own every byte it asserts on.
const PROBE_RESUME = {
  contact: {
    name: "Probe Candidate", headline: "Site Reliability Engineer",
    email: "probe@example.com", phone: "+91 90000 00000",
    location: "Pune, India", links: ["github.com/probe"],
  },
  summary: {
    text: "Site reliability engineer with six years running production "
      + "infrastructure for high-traffic services. Focused on reducing "
      + "operational toil through automation and better alerting.",
  },
  experience: [
    {
      company: "Acme Corp", role: "Senior SRE", location: "Pune, India",
      start_date: "Mar 2021", end_date: "", current: true,
      bullets: [
        "Led migration of 40 services to Kubernetes, cutting deploy time by 65%",
        "Built Terraform modules adopted by 8 engineering teams",
        "Reduced pager volume by 45% by rewriting alert rules around SLOs",
      ],
    },
    {
      company: "Globex", role: "Infrastructure Engineer",
      location: "Bengaluru, India",
      start_date: "Jul 2019", end_date: "Feb 2021", current: false,
      bullets: [
        "Automated PostgreSQL failover, improving recovery time to under 2 minutes",
        "Introduced CI pipelines that cut build times from 22 to 7 minutes",
      ],
    },
  ],
  projects: [{
    name: "kubewatch",
    bullets: ["Open-source controller that reports drift in cluster manifests"],
  }],
  education: [{
    school: "COEP Pune", degree: "B.Tech", field_of_study: "Computer Engineering",
    start_date: "2015", end_date: "2019", current: false,
  }],
  skills: [
    { label: "Platform", items: ["Kubernetes", "Docker", "Terraform", "AWS"] },
    { label: "Languages", items: ["Python", "Go", "Bash"] },
  ],
};

const fd = new FormData();
const txt = [
  PROBE_RESUME.contact.name,
  PROBE_RESUME.contact.email,
  "Experienced engineer.",
].join("\n");
fd.append("file", new Blob([txt], { type: "text/plain" }), "probe.txt");
fd.append("title", `API Surface Probe ${Date.now()}`);
const uploaded = (await api.post("/resumes/upload", fd)).data;
ok(uploaded.parse_status === "ready", "POST /resumes/upload", uploaded.parse_status);

// Give it real content so analysis/tailoring have something to work with.
await api.put(`/resumes/${uploaded.id}/data`, { structured_data: PROBE_RESUME });
const BASE_ID = uploaded.id;
const one = (await api.get(`/resumes/${BASE_ID}`)).data;
hasFields(one, ["id", "user_id", "title", "kind", "parent_id", "tailored_for", "storage_key", "template_key", "structured_data", "parse_status", "parse_note", "version_cursor", "updated_at"], "GET /resumes/{id}");

const sd = one.structured_data;
hasFields(sd, ["contact", "summary", "experience", "projects", "education", "skills"], "structured_data sections");
hasFields(sd.contact, ["name", "email", "phone", "location", "links"], "contact shape");

const blank = (await api.post("/resumes", { title: "Surface Probe" })).data;
ok(blank.kind === "base", "POST /resumes -> kind=base", blank.kind);

const ps = (await api.get(`/resumes/${BASE_ID}/parse-status`)).data;
hasFields(ps, ["resume_id", "parse_status"], "GET /parse-status");

const renamed = (await api.patch(`/resumes/${blank.id}`, { title: "Renamed" })).data;
ok(renamed.title === "Renamed", "PATCH /resumes/{id} renames");
await expectStatus(api.patch(`/resumes/${blank.id}`, { title: "  " }), 422, "PATCH empty title -> 422");

const edited = structuredClone(sd);
edited.summary.text = "Probe-edited summary.";
const put = (await api.put(`/resumes/${blank.id}/data`, { structured_data: edited })).data;
ok(put.structured_data.summary.text === "Probe-edited summary.", "PUT /data persists");

const tpl = (await api.post(`/resumes/${blank.id}/template`, { template_key: "executive" })).data;
ok(tpl.template_key === "executive", "POST /template applies");
await expectStatus(api.post(`/resumes/${blank.id}/template`, { template_key: "nope" }), 404, "unknown template -> 404");

/* --------------------------------------------------------------- fork --- */
section("fork");
const fork = (await api.post(`/resumes/${BASE_ID}/fork`, {})).data;
ok(fork.id !== BASE_ID, "POST /fork makes a new row");
ok(fork.parent_id === BASE_ID, "child.parent_id = base");
ok(fork.kind === "tailored", "child.kind=tailored", fork.kind);
ok(JSON.stringify(fork.structured_data) === JSON.stringify(sd), "child deep-copies structured_data");

/* ----------------------------------------------------------- analysis --- */
section("analysis");
await expectStatus(api.get(`/resumes/${blank.id}/analysis`), 404, "GET /analysis before run -> 404 (normal)");
const an = (await api.post(`/resumes/${BASE_ID}/analyze`)).data;
hasFields(an, ["id", "resume_id", "overall_score", "category_scores", "role_tags", "commentary", "ats_review", "created_at"], "POST /analyze");
hasFields(an.commentary, ["headline", "category_notes"], "commentary shape");
hasFields(an.ats_review, ["expansions", "note"], "ats_review shape");
ok(Array.isArray(an.ats_review.expansions), "ats_review expansions is an array");
ok(an.ats_review.relabels === undefined, "ats_review.relabels is gone (feature deleted 2026-10-11)");
// Operator data -- node names, model ids, latencies -- stays server-side.
ok(an.trace === undefined, "trace is not exposed to the client");
const cats = Object.keys(an.category_scores).sort();
ok(JSON.stringify(cats) === JSON.stringify(["contact", "experience", "format", "summary"]), "4 categories", cats.join(","));
hasFields(an.category_scores.contact, ["score", "max", "notes"], "category shape");
const maxes = Object.entries(an.category_scores).map(([k, v]) => `${k}:${v.max}`).sort();
ok(JSON.stringify(maxes) === JSON.stringify(["contact:15", "experience:45", "format:20", "summary:20"]), "weights 15/20/45/20", maxes.join(" "));
ok(an.overall_score >= 0 && an.overall_score <= 100, "score in range", `${an.overall_score}`);
const an2 = (await api.get(`/resumes/${BASE_ID}/analysis`)).data;
ok(an2.id === an.id, "GET /analysis returns the latest");
ok(JSON.stringify(an2.ats_review) === JSON.stringify(an.ats_review), "ats_review survives the DB round-trip");

/* ------------------------------------------------------- guided steps --- */
section("analysis/steps");
// Unlike /analysis this must work before anything has been analysed, because
// the guided flow is what produces the first score.
const st0 = (await api.get(`/resumes/${blank.id}/analysis/steps`)).data;
hasFields(st0, ["resume_id", "overall_score", "max_score", "points_available", "steps", "commentary", "commentary_stale"], "GET /analysis/steps");
ok(st0.max_score === 100, "steps max_score = 100", `${st0.max_score}`);

const st = (await api.get(`/resumes/${BASE_ID}/analysis/steps`)).data;
const stepIds = st.steps.map((x) => x.id);
hasFields(st.commentary, ["headline", "category_notes"], "steps commentary shape");
{
  // `strengths` is optional by design: reports written before the field
  // existed lack the key, and the rule-engine fallback returns {}. So the
  // assertion is about SHAPE when present, not presence.
  const strengths = st.commentary.strengths;
  ok(strengths === undefined || (typeof strengths === "object" && !Array.isArray(strengths)),
     "commentary.strengths is absent or an object", typeof strengths);
  const bad = Object.entries(strengths ?? {}).filter(
    ([, v]) => !Array.isArray(v) || v.some((x) => typeof x !== "string"),
  );
  ok(bad.length === 0, "every strengths value is string[]", bad.map(([k]) => k).join(",") || "ok");
  const over = Object.entries(strengths ?? {}).filter(([, v]) => v.length > 3);
  ok(over.length === 0, "strengths capped at 3 per category", over.map(([k]) => k).join(",") || "ok");
}
// The guided editor is the screen that renders this prose. It was generated
// and persisted on every analysis for months and returned to nobody; if it
// silently drops off this response again, that regression is invisible.
ok(typeof st.commentary_stale === "boolean",
   "commentary_stale must be a real boolean -- undefined would render as 'fresh'");
ok(JSON.stringify(stepIds) === JSON.stringify(["contact", "summary", "experience", "format", "extras"]), "5 steps in order, extras last", stepIds.join(","));
ok(st.steps.every((x, i) => x.index === i), "step.index matches position");
ok(st.overall_score === st.steps.reduce((a, x) => a + x.score, 0), "overall = sum of step scores", `${st.overall_score}`);
ok(st.points_available === st.steps.reduce((a, x) => a + x.points_available, 0), "points_available = sum of steps");
ok(st.steps.every((x) => x.points_available === x.findings.reduce((a, f) => a + f.points, 0)), "step points = sum of finding points");
ok(st.steps.every((x) => x.score + x.points_available <= x.max), "findings never promise more than the category headroom");
ok(st.steps.every((x) => x.finding_count === x.findings.length), "finding_count matches findings[]");

const allFindings = st.steps.flatMap((x) => x.findings);
ok(allFindings.length > 0, "sample resume yields findings", `${allFindings.length}`);
hasFields(allFindings[0], ["id", "category", "target_ref", "severity", "points", "message", "fix_hint", "action", "meta"], "finding shape");
ok(allFindings.every((f) => f.points > 0), "no zero-point findings");
ok(allFindings.every((f) => ["high", "medium", "low"].includes(f.severity)), "severity is one of high|medium|low");
ok(new Set(allFindings.map((f) => f.id)).size === allFindings.length, "finding ids are unique");
ok(st.steps.every((x) => x.findings.every((f) => f.category === x.id)), "findings sit under their own category");

// Backward compatibility: the old notes[] must still be derived from findings.
ok(Array.isArray(an.category_scores.contact.notes), "category_scores.notes survives");

// THE load-bearing assertion: "+N points" must be the literal score delta.
// Break a field with a known worth, confirm the finding appears with that
// value, fix it, and require the score to move by exactly N.
const probeSd = JSON.parse(JSON.stringify(sd));
probeSd.contact = { ...probeSd.contact, phone: "" };
await api.put(`/resumes/${blank.id}/data`, { structured_data: probeSd });
const broken = (await api.get(`/resumes/${blank.id}/analysis/steps`)).data;
const phoneCard = broken.steps.flatMap((x) => x.findings).find((f) => f.id === "contact.phone.missing");
ok(Boolean(phoneCard), "removing the phone raises contact.phone.missing");
if (phoneCard) {
  const fixedSd = JSON.parse(JSON.stringify(probeSd));
  fixedSd.contact.phone = "+91 90000 00000";
  await api.put(`/resumes/${blank.id}/data`, { structured_data: fixedSd });
  const healed = (await api.get(`/resumes/${blank.id}/analysis/steps`)).data;
  ok(healed.overall_score - broken.overall_score === phoneCard.points,
    `fixing a "+${phoneCard.points}" finding moves the score by exactly ${phoneCard.points}`,
    `${broken.overall_score} -> ${healed.overall_score}`);
  ok(!healed.steps.flatMap((x) => x.findings).some((f) => f.id === "contact.phone.missing"),
    "the finding disappears once fixed");
} else {
  ok(false, "SKIPPED points-delta check (no phone finding)");
  ok(false, "SKIPPED disappearance check (no phone finding)");
}

// Steps reflect edits with no re-analyse, which is what makes the editor live.
const stale = (await api.get(`/resumes/${BASE_ID}/analysis`)).data.overall_score;
ok(typeof stale === "number", "stored report keeps its own score", `${stale}`);

/* ------------------------------------------------------------- tailor --- */
section("tailor");
const sess = (await api.post(`/resumes/${BASE_ID}/tailor`, { jd_title: "SRE at Probe", jd_content: jd.content })).data;
hasFields(sess, ["id", "resume_id", "job_description_id", "match_percent", "baseline_percent", "matched_keywords", "gap_keywords", "thread_id", "graph_state", "created_at"], "POST /tailor");
// Tailoring is IN PLACE: the session must stay on the resume we asked about,
// otherwise the Analysis and Tailor tabs edit different rows and an accepted
// suggestion appears to do nothing.
ok(sess.resume_id === BASE_ID, "tailoring is IN PLACE (session.resume_id is the base)");

// Everything downstream works through the session above, so CHILD must stay
// "the resume `sess` edits" -- which in-place means the base itself.
const CHILD = sess.resume_id;

// Forking still exists, opt-in. Its child also outlives this section: the
// delete-semantics section needs a real child of BASE_ID to orphan.
const forkSess = (await api.post(`/resumes/${BASE_ID}/tailor`, { jd_title: "Fork probe", jd_content: jd.content, fork: true })).data;
ok(forkSess.resume_id !== BASE_ID, "fork:true still forks");
const TAILOR_CHILD = forkSess.resume_id;
const forked = (await api.get(`/resumes/${TAILOR_CHILD}`)).data;
ok(forked.kind === "tailored" && forked.parent_id === BASE_ID, "forked child linked to base");
ok(typeof forked.tailored_for === "string" && forked.tailored_for.length > 0, "tailored_for set", forked.tailored_for);

const sessions = (await api.get(`/resumes/${CHILD}/tailor`)).data;
ok(Array.isArray(sessions) && sessions.length >= 1, "GET /tailor lists sessions", `${sessions.length}`);

const buckets = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
const bucketNames = Object.keys(buckets).sort();
ok(JSON.stringify(bucketNames) === JSON.stringify(["active", "matched", "rejected"]), "bucket names", bucketNames.join(","));
const all = [...buckets.active, ...buckets.matched, ...buckets.rejected];
if (all.length) {
  hasFields(all[0], ["id", "session_id", "resume_id", "origin", "section", "target_ref", "placement", "original_text", "suggested_text", "edited_text", "keywords", "reasoning", "status", "grounded", "critic_notes", "revisions", "score_delta", "question", "answer", "attested", "requires_text"], "SuggestionOut shape");
  // Same currency as the Analysis tab. A missing field renders as NaN points.
  ok(all.every((x) => typeof x.score_delta === "number"),
     "score_delta must be a number on every suggestion");
  // The review gate rejects any draft that introduces a finding, and
  // `delta score == points` holds, so a negative delta means that gate has a
  // hole. Assert it here so the hole surfaces in CI, not in a resume.
  ok(all.every((x) => x.score_delta >= 0),
     "no suggestion may LOWER the resume score");
  const statuses = [...new Set(all.map((s) => s.status))];
  ok(statuses.every((s) => ["pending", "accepted", "rejected", "needs_input"].includes(s)), "status vocabulary", statuses.join(","));
  // `active` is open items, which now means drafts AND unanswered questions.
  ok(buckets.active.every((s) => ["pending", "needs_input"].includes(s.status)), "active bucket holds open rows");
}

/* -------------------------------------------------------- suggestions --- */
section("suggestions");
ok(buckets.active.filter((s) => s.status === "pending").length >= 3, "enough draft suggestions to test accept/reject/edit", `${buckets.active.filter((s) => s.status === "pending").length}`);
// Questions lead `active`, and a question cannot be accepted or edited --
// it has no draft. This section is about the accept/reject/edit lifecycle,
// so it must pick a DRAFT. Taking active[0] blindly started 422-ing the
// moment question cards began sorting first.
const drafts = buckets.active.filter((s) => s.status === "pending");
if (drafts.length) {
  const target = drafts[0];
  const before = (await api.get(`/resumes/${CHILD}`)).data;
  const acc = (await api.patch(`/suggestions/${target.id}`, { action: "accept" })).data;
  ok(acc.status === "accepted", "PATCH accept -> accepted", acc.status);
  const after = (await api.get(`/resumes/${CHILD}`)).data;
  ok(JSON.stringify(before.structured_data) !== JSON.stringify(after.structured_data), "accept MUTATES the resume");
  ok(after.version_cursor > before.version_cursor, "accept bumps version_cursor", `${before.version_cursor}->${after.version_cursor}`);

  const rest = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
  const restDrafts = rest.active.filter((s) => s.status === "pending");
  if (restDrafts.length) {
    const rej = (await api.patch(`/suggestions/${restDrafts[0].id}`, { action: "reject" })).data;
    ok(rej.status === "rejected", "PATCH reject -> rejected");
    const afterRej = (await api.get(`/resumes/${CHILD}`)).data;
    ok(JSON.stringify(afterRej.structured_data) === JSON.stringify(after.structured_data), "reject does NOT mutate the resume");
  }
  const rest2 = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
  const rest2Drafts = rest2.active.filter((s) => s.status === "pending");
  if (ok(rest2Drafts.length > 0, "a draft remains for the edit test")) {
    const ed = (await api.patch(`/suggestions/${rest2Drafts[0].id}`, { action: "edit", edited_text: "My own wording." })).data;
    ok(ed.edited_text === "My own wording.", "PATCH edit stores edited_text");
  }
  await expectStatus(api.patch(`/suggestions/${target.id}`, { action: "banana" }), 422, "invalid action -> 422");
}

/* ----------------------------------------------------------- versions --- */
section("versions");
const vers = (await api.get(`/resumes/${CHILD}/versions`)).data;
hasFields(vers[0], ["id", "resume_id", "seq", "label", "created_at"], "GET /versions");
const curBefore = (await api.get(`/resumes/${CHILD}`)).data.version_cursor;
const undo = (await api.post(`/resumes/${CHILD}/undo`)).data;
ok(undo.version_cursor < curBefore, "POST /undo walks back", `${curBefore}->${undo.version_cursor}`);
const redo = (await api.post(`/resumes/${CHILD}/redo`)).data;
ok(redo.version_cursor === curBefore, "POST /redo walks forward", `${redo.version_cursor}`);

/* ------------------------------------------------------------- export --- */
section("export");
for (const fmt of ["pdf", "docx", "txt"]) {
  const r = await api.get(`/resumes/${CHILD}/export`, { params: { format: fmt }, responseType: "arraybuffer" });
  const bytes = new Uint8Array(r.data);
  const sig = { pdf: [0x25, 0x50, 0x44, 0x46], docx: [0x50, 0x4b, 0x03, 0x04] }[fmt];
  const good = sig ? sig.every((b, i) => bytes[i] === b) : bytes.length > 0;
  ok(good, `GET /export?format=${fmt}`, `${bytes.length} bytes`);
}
await expectStatus(api.get(`/resumes/${CHILD}/export`, { params: { format: "rtf" } }), 422, "unknown format -> 422");

/* ------------------------------------------------- isolation + delete --- */
section("cross-user isolation");
for (const [label, make] of [
  ["GET resume", () => asOther.get(`/resumes/${BASE_ID}`)],
  ["GET analysis", () => asOther.get(`/resumes/${BASE_ID}/analysis`)],
  ["GET analysis/steps", () => asOther.get(`/resumes/${BASE_ID}/analysis/steps`)],
  ["GET chat", () => asOther.get(`/resumes/${BASE_ID}/chat`)],
  ["GET versions", () => asOther.get(`/resumes/${BASE_ID}/versions`)],
  ["GET parse-status", () => asOther.get(`/resumes/${BASE_ID}/parse-status`)],
  ["GET export", () => asOther.get(`/resumes/${BASE_ID}/export`, { params: { format: "txt" } })],
  ["POST analyze", () => asOther.post(`/resumes/${BASE_ID}/analyze`)],
  ["POST tailor", () => asOther.post(`/resumes/${BASE_ID}/tailor`, { jd_content: "x" })],
  ["POST chat", () => asOther.post(`/resumes/${BASE_ID}/chat`, { content: "x" })],
  ["PUT data", () => asOther.put(`/resumes/${BASE_ID}/data`, { structured_data: {} })],
  ["POST template", () => asOther.post(`/resumes/${BASE_ID}/template`, { template_key: "modern" })],
  ["POST undo", () => asOther.post(`/resumes/${BASE_ID}/undo`)],
  ["PATCH rename", () => asOther.patch(`/resumes/${BASE_ID}`, { title: "hijack" })],
  ["POST fork", () => asOther.post(`/resumes/${BASE_ID}/fork`, {})],
  ["POST rerank", () => asOther.post(`/resumes/${BASE_ID}/sections/experience/rerank`)],
  ["DELETE", () => asOther.delete(`/resumes/${BASE_ID}`)],
]) {
  // Lazily: an eagerly-created rejected promise is an unhandled rejection
  // before the loop reaches its await.
  await expectStatus(make(), 404, `other user ${label} -> 404`);
}

section("rerank — POST /resumes/{id}/sections/{ref}/rerank (deterministic, no keys needed)");
{
  const scratch = (await api.post("/resumes", { title: "Rerank probe" })).data;
  const outOfOrder = {
    contact: { name: "Probe", email: "p@x.io", phone: "+1 555 0100", links: [] },
    summary: { text: "Engineer with six years of backend and platform work across cloud services." },
    experience: [
      { company: "Oldcorp", role: "Junior", start_date: "2017", end_date: "2019", bullets: ["Shipped billing."] },
      { company: "Newcorp", role: "Senior", start_date: "2021", end_date: "2023", bullets: ["Cut build times 65%."] },
    ],
    projects: [], education: [], skills: [{ label: "Languages", items: ["Python"] }],
  };
  await api.put(`/resumes/${scratch.id}/data`, { structured_data: outOfOrder });

  const before = (await api.get(`/resumes/${scratch.id}/analysis/steps`)).data;
  const chrono = before.steps
    .flatMap((st) => st.findings)
    .find((f) => f.id === "ats.not_reverse_chronological");
  ok(Boolean(chrono), "fixture trips ats.not_reverse_chronological");
  ok(chrono?.action === "rerank", "finding carries action=rerank", chrono?.action);

  const after = (await api.post(`/resumes/${scratch.id}/sections/experience/rerank`)).data;
  const roles = after.structured_data.experience.map((e) => e.role);
  ok(JSON.stringify(roles) === JSON.stringify(["Senior", "Junior"]), "rerank sorts newest-first", roles.join(","));

  const steps2 = (await api.get(`/resumes/${scratch.id}/analysis/steps`)).data;
  const still = steps2.steps.flatMap((st) => st.findings).some((f) => f.id === "ats.not_reverse_chronological");
  ok(!still, "the finding is gone after rerank");
  ok(steps2.overall_score > before.overall_score, "score rose", `${before.overall_score} -> ${steps2.overall_score}`);

  await expectStatus(
    api.post(`/resumes/${scratch.id}/sections/summary.text/rerank`),
    422,
    "non-reorderable ref -> 422",
  );

  await api.delete(`/resumes/${scratch.id}`);
}

section("clarifying questions — POST /suggestions/{id}/answer (no keys needed)");
{
  // Above the AI-rewrite section on purpose: without provider keys that
  // section aborts the run, so anything placed after it never executes.
  // The session the tailor section created above, on the live JD -- not the
  // seeded one. BASE_ID is this probe's own upload and has no session, and
  // reading seed data would test the seeder rather than the endpoint.
  const fresh = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
  {
    const buckets = fresh;
    const questions = (buckets.active ?? []).filter((s) => s.status === "needs_input");
    ok(questions.length > 0, "tailoring produced question cards", String(questions.length));
    ok(
      (buckets.active ?? [])[0]?.status === "needs_input",
      "questions lead the active bucket",
      (buckets.active ?? [])[0]?.status,
    );

    const q = questions[0];
    hasFields(q, ["question", "answer", "attested", "requires_text"], "question card shape");
    ok(q.question.length > 0, "a needs_input card carries question text");
    ok(q.suggested_text === "", "a question has no draft", JSON.stringify(q.suggested_text));
    ok(q.grounded === false, "a question is not grounded", String(q.grounded));
    // The anti-leading rule, asserted on the wire rather than trusted.
    ok(
      /leave (this |it )?blank/i.test(q.question),
      "the question states the do-nothing option",
      q.question.slice(0, 60),
    );

    // A question cannot be accepted: there is nothing to apply. axios
    // rejects on 4xx, so these go through expectStatus, not a status read.
    await expectStatus(
      api.patch(`/suggestions/${q.id}`, { action: "accept" }),
      422,
      "accepting a question is refused",
    );

    const needsText = questions.find((x) => x.requires_text);
    if (needsText) {
      await expectStatus(
        api.post(`/suggestions/${needsText.id}/answer`, { confirmed: true }),
        422,
        "a bare yes cannot attest a duration",
      );
    }

    // Declining is a 200 that rejects, not an error -- so this one is a
    // plain await and would throw if the server ever made it a 4xx.
    const declinable = questions[questions.length - 1];
    const declined = (await api.post(`/suggestions/${declinable.id}/answer`, { answer: "" })).data;
    ok(declined.status === "rejected", "a declined question is rejected", declined.status);
    ok(declined.suggested_text === "", "a decline produces no draft");
    ok(declined.attested === false, "a decline attests nothing");
  }
}

section("AI rewrite — POST /resumes/{id}/sections/{ref}/rewrite");
if (!LLM_ON) {
  skip("no provider key configured (health.llm_configured=false)");
} else {
  // Its own resume, so this block owns every byte it asserts on.
  const rw = (await api.post("/resumes", { title: `Rewrite probe ${Date.now()}` })).data;
  await api.put(`/resumes/${rw.id}/data`, {
    structured_data: {
      contact: { name: "Jane", email: "j@x.com", phone: "555", location: "Pune", links: ["gh"] },
      summary: { text: "" },
      experience: [{
        company: "Acme", role: "Platform Engineer", dates: "2020-2024",
        // Two weak bullets: accepting a rewrite makes that bullet strong, and
        // the rule engine rightly refuses to "improve" already-strong text.
        // The staleness case therefore needs its own untouched bullet.
        bullets: ["Worked on the deployment pipeline", "Helped with the monitoring stack"],
      }],
      skills: [{ label: "P", items: ["Python", "Terraform"] }],
      education: [{ school: "S", degree: "BSc", dates: "2016" }],
      projects: [],
    },
  });

  const REF = "exp_0.bullet_0";
  const first = (await api.post(
    `/resumes/${rw.id}/sections/${REF}/rewrite`,
    { finding_id: "experience.weak_verbs" },
  )).data;
  hasFields(first, ["id", "origin", "session_id", "status", "original_text",
                    "suggested_text", "keywords", "reasoning"], "rewrite returns SuggestionOut");
  ok(first.origin === "analysis", "rewrite origin=analysis", first.origin);
  ok(first.session_id === null, "rewrite has no session", String(first.session_id));
  ok(first.status === "pending", "rewrite is pending", first.status);
  ok(first.original_text === "Worked on the deployment pipeline",
     "original_text copied verbatim", first.original_text);
  ok(Boolean(first.suggested_text?.trim()), "rewrite produced text");

  const again = (await api.post(
    `/resumes/${rw.id}/sections/${REF}/rewrite`,
    { finding_id: "experience.weak_verbs" },
  )).data;
  ok(again.id === first.id, "re-clicking returns the SAME draft, not a duplicate");

  const regen = (await api.post(
    `/resumes/${rw.id}/sections/${REF}/rewrite`,
    { finding_id: "experience.weak_verbs", regenerate: true },
  )).data;
  ok(regen.id !== first.id, "regenerate supersedes the previous draft");

  // A writer that was never reached must not produce a verdict about the
  // resume. 503 (upstream, transient, retryable) vs 422 ("your request cannot
  // be processed") -- conflating them once told a user their resume needed a
  // number they did not have, when the model had simply been rate-limited.
  await expectStatus(
    api.post(`/resumes/${rw.id}/sections/${REF}/rewrite?simulate=unavailable`, {}),
    503,
    "unreachable writer -> 503, not 422",
  );
  try {
    await api.post(`/resumes/${rw.id}/sections/${REF}/rewrite?simulate=unavailable`, {});
    ok(false, "503 detail names the transport failure");
  } catch (e) {
    const detail = String(e.response?.data?.detail ?? "");
    ok(/could not be reached/i.test(detail),
       "503 detail names the transport failure", detail.slice(0, 60));
    ok(!/fact only you have/i.test(detail),
       "503 is not phrased as a verdict about the resume");
  }

  await expectStatus(
    api.post(`/resumes/${rw.id}/sections/experience/rewrite`, {}),
    422, "container ref (unresolvable) -> 422");
  await expectStatus(
    asOther.post(`/resumes/${rw.id}/sections/${REF}/rewrite`, {}),
    404, "other user rewrite -> 404");

  // The 409 staleness guard covers analysis-origin drafts too. Do this on the
  // SECOND bullet, before anything is accepted, so the text is still weak.
  const stale = (await api.post(
    `/resumes/${rw.id}/sections/exp_0.bullet_1/rewrite`,
    { finding_id: "experience.weak_verbs" },
  )).data;
  const doc = (await api.get(`/resumes/${rw.id}`)).data.structured_data;
  doc.experience[0].bullets[1] = "My own hand edit.";
  await api.put(`/resumes/${rw.id}/data`, { structured_data: doc });
  await expectStatus(
    api.patch(`/suggestions/${stale.id}`, { action: "accept" }),
    409, "stale analysis draft -> 409");
  const kept = (await api.get(`/resumes/${rw.id}`)).data;
  ok(kept.structured_data.experience[0].bullets[1] === "My own hand edit.",
     "the hand edit survived the conflict");

  // The draft must feed the ORDINARY suggestion lifecycle, not a second one.
  const accepted = (await api.patch(`/suggestions/${regen.id}`, { action: "accept" })).data;
  ok(accepted.status === "accepted", "analysis draft accepts like any suggestion", accepted.status);
  const after = (await api.get(`/resumes/${rw.id}`)).data;
  ok(after.structured_data.experience[0].bullets[0] === regen.suggested_text,
     "accepting an analysis draft patches the resume");

  await api.delete(`/resumes/${rw.id}`);
}

section("extras \u2014 optional sections that must never move a score");
{
  const ex = (await api.post("/resumes", { title: "Extras probe" })).data;
  const doc = (await api.get(`/resumes/${ex.id}`)).data.structured_data;
  doc.contact = { ...doc.contact, name: "Extra Tester", email: "e@example.com" };
  doc.experience = [{
    company: "Acme", role: "Engineer", dates: "2020-2024",
    bullets: ["Delivered a 40% latency reduction across 3 services"],
  }];
  await api.put(`/resumes/${ex.id}/data`, { structured_data: doc });

  const before = (await api.get(`/resumes/${ex.id}/analysis/steps`)).data;
  ok(before.steps.length === 5, "five steps", `${before.steps.length}`);
  const extrasStep = before.steps[4];
  ok(extrasStep.id === "extras", "fifth step is extras", extrasStep.id);
  ok(extrasStep.status === "optional", "extras status is 'optional'", extrasStep.status);
  ok(extrasStep.score === 0 && extrasStep.max === 0 && extrasStep.points_available === 0,
     "extras is zeroed on every axis");
  ok(extrasStep.findings.length === 0, "extras emits no findings");
  ok(!("scored" in extrasStep), "the internal `scored` flag does not leak");
  ok(before.max_score === 100, "max_score is still 100", `${before.max_score}`);
  ok(before.steps.reduce((t, s) => t + s.max, 0) === 100, "step maxima still sum to 100");

  // The whole point of an unscored step, asserted rather than assumed.
  doc.extras = [
    { kind: "certifications", title: "Certifications",
      entries: [{ primary: "AWS Solutions Architect", secondary: "Amazon",
                  date: "2023", detail: "" }] },
    { kind: "languages", title: "Languages",
      entries: [{ primary: "Hindi", secondary: "Native", date: "", detail: "" }] },
  ];
  await api.put(`/resumes/${ex.id}/data`, { structured_data: doc });
  const after = (await api.get(`/resumes/${ex.id}/analysis/steps`)).data;
  ok(after.overall_score === before.overall_score,
     "adding extras does not move overall_score",
     `${before.overall_score} -> ${after.overall_score}`);
  ok(after.points_available === before.points_available,
     "adding extras does not move points_available");

  const stored = (await api.get(`/resumes/${ex.id}`)).data.structured_data;
  ok(stored.extras.length === 2, "both sections round-tripped", `${stored.extras.length}`);
  ok(stored.extras[0].kind === "certifications" && stored.extras[1].kind === "languages",
     "section ORDER survived the round trip");
  ok(stored.extras[0].entries[0].date === "2023", "entry fields survived");

  // Exports must carry them, or the editor is lying about what ships.
  const txt = (await api.get(`/resumes/${ex.id}/export?format=txt`)).data;
  ok(String(txt).includes("AWS Solutions Architect"), "extras reach the TXT export");

  await api.delete(`/resumes/${ex.id}`);
}

section("delete semantics");
const delChild = (await api.delete(`/resumes/${fork.id}`)).data;
hasFields(delChild, ["deleted", "vectors_removed", "objects_removed", "children_orphaned"], "DeleteOut shape");
const delBase = (await api.delete(`/resumes/${BASE_ID}`)).data;
ok(delBase.children_orphaned >= 1, "deleting a base ORPHANS children", `${delBase.children_orphaned}`);
// The tailor fork, not CHILD: in-place mode makes CHILD the base, and the
// base was just deleted.
const survivor = (await api.get(`/resumes/${TAILOR_CHILD}`)).data;
ok(survivor.parent_id === null, "orphaned child survives with parent_id=null");

const tally = skipped ? ` (${skipped} section(s) SKIPPED)` : "";
console.log(fail ? `\n>>> ${fail} FAILURES (${pass} passed)${tally}` : `\n>>> all ${pass} checks passed${tally}`);
process.exit(fail ? 1 : 0);
