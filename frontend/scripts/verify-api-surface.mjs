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

const users = (await axios.get(`${BASE}/users`)).data;
const USER = users[0].id;
const OTHER = users[1].id;
const api = axios.create({ baseURL: BASE, headers: { "X-User-Id": USER } });
const asOther = axios.create({ baseURL: BASE, headers: { "X-User-Id": OTHER } });

/* ------------------------------------------------------------ system ---- */
section("system");
const health = (await api.get("/health")).data;
hasFields(health, ["status", "database", "pgvector", "storage", "llm_configured", "model", "users"], "GET /health");
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
hasFields(an, ["id", "resume_id", "overall_score", "category_scores", "role_tags", "created_at"], "POST /analyze");
const cats = Object.keys(an.category_scores).sort();
ok(JSON.stringify(cats) === JSON.stringify(["contact", "experience", "format", "summary"]), "4 categories", cats.join(","));
hasFields(an.category_scores.contact, ["score", "max", "notes"], "category shape");
const maxes = Object.entries(an.category_scores).map(([k, v]) => `${k}:${v.max}`).sort();
ok(JSON.stringify(maxes) === JSON.stringify(["contact:15", "experience:45", "format:20", "summary:20"]), "weights 15/20/45/20", maxes.join(" "));
ok(an.overall_score >= 0 && an.overall_score <= 100, "score in range", `${an.overall_score}`);
const an2 = (await api.get(`/resumes/${BASE_ID}/analysis`)).data;
ok(an2.id === an.id, "GET /analysis returns the latest");

/* ------------------------------------------------------- guided steps --- */
section("analysis/steps");
// Unlike /analysis this must work before anything has been analysed, because
// the guided flow is what produces the first score.
const st0 = (await api.get(`/resumes/${blank.id}/analysis/steps`)).data;
hasFields(st0, ["resume_id", "overall_score", "max_score", "points_available", "steps"], "GET /analysis/steps");
ok(st0.max_score === 100, "steps max_score = 100", `${st0.max_score}`);

const st = (await api.get(`/resumes/${BASE_ID}/analysis/steps`)).data;
const stepIds = st.steps.map((x) => x.id);
ok(JSON.stringify(stepIds) === JSON.stringify(["contact", "summary", "experience", "format"]), "4 steps in scoring order", stepIds.join(","));
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
ok(sess.resume_id !== BASE_ID, "tailoring FORKED (session.resume_id is the child)");
const CHILD = sess.resume_id;
const child = (await api.get(`/resumes/${CHILD}`)).data;
ok(child.kind === "tailored" && child.parent_id === BASE_ID, "child linked to base");
ok(typeof child.tailored_for === "string" && child.tailored_for.length > 0, "tailored_for set", child.tailored_for);

const sessions = (await api.get(`/resumes/${CHILD}/tailor`)).data;
ok(Array.isArray(sessions) && sessions.length >= 1, "GET /tailor lists sessions", `${sessions.length}`);

const buckets = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
const bucketNames = Object.keys(buckets).sort();
ok(JSON.stringify(bucketNames) === JSON.stringify(["active", "matched", "rejected"]), "bucket names", bucketNames.join(","));
const all = [...buckets.active, ...buckets.matched, ...buckets.rejected];
if (all.length) {
  hasFields(all[0], ["id", "session_id", "resume_id", "origin", "section", "target_ref", "placement", "original_text", "suggested_text", "edited_text", "keywords", "reasoning", "status", "grounded", "critic_notes", "revisions"], "SuggestionOut shape");
  const statuses = [...new Set(all.map((s) => s.status))];
  ok(statuses.every((s) => ["pending", "accepted", "rejected"].includes(s)), "status vocabulary is pending/accepted/rejected", statuses.join(","));
  ok(buckets.active.every((s) => s.status === "pending"), "active bucket holds pending rows");
}

/* -------------------------------------------------------- suggestions --- */
section("suggestions");
ok(buckets.active.length >= 3, "enough active suggestions to test accept/reject/edit", `${buckets.active.length}`);
if (buckets.active.length) {
  const target = buckets.active[0];
  const before = (await api.get(`/resumes/${CHILD}`)).data;
  const acc = (await api.patch(`/suggestions/${target.id}`, { action: "accept" })).data;
  ok(acc.status === "accepted", "PATCH accept -> accepted", acc.status);
  const after = (await api.get(`/resumes/${CHILD}`)).data;
  ok(JSON.stringify(before.structured_data) !== JSON.stringify(after.structured_data), "accept MUTATES the resume");
  ok(after.version_cursor > before.version_cursor, "accept bumps version_cursor", `${before.version_cursor}->${after.version_cursor}`);

  const rest = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
  if (rest.active.length) {
    const rej = (await api.patch(`/suggestions/${rest.active[0].id}`, { action: "reject" })).data;
    ok(rej.status === "rejected", "PATCH reject -> rejected");
    const afterRej = (await api.get(`/resumes/${CHILD}`)).data;
    ok(JSON.stringify(afterRej.structured_data) === JSON.stringify(after.structured_data), "reject does NOT mutate the resume");
  }
  const rest2 = (await api.get(`/resumes/${CHILD}/tailor/${sess.id}/suggestions`)).data;
  if (ok(rest2.active.length > 0, "a suggestion remains for the edit test")) {
    const ed = (await api.patch(`/suggestions/${rest2.active[0].id}`, { action: "edit", edited_text: "My own wording." })).data;
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
  ["DELETE", () => asOther.delete(`/resumes/${BASE_ID}`)],
]) {
  // Lazily: an eagerly-created rejected promise is an unhandled rejection
  // before the loop reaches its await.
  await expectStatus(make(), 404, `other user ${label} -> 404`);
}

section("delete semantics");
const delChild = (await api.delete(`/resumes/${fork.id}`)).data;
hasFields(delChild, ["deleted", "vectors_removed", "objects_removed", "children_orphaned"], "DeleteOut shape");
const delBase = (await api.delete(`/resumes/${BASE_ID}`)).data;
ok(delBase.children_orphaned >= 1, "deleting a base ORPHANS children", `${delBase.children_orphaned}`);
const survivor = (await api.get(`/resumes/${CHILD}`)).data;
ok(survivor.parent_id === null, "orphaned child survives with parent_id=null");

console.log(fail ? `\n>>> ${fail} FAILURES (${pass} passed)` : `\n>>> all ${pass} checks passed`);
process.exit(fail ? 1 : 0);
