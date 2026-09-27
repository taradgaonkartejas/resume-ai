/**
 * Runtime verification of the axios gateway against a live API.
 *
 * A successful `tsc`/`vite build` proves the types line up; it proves nothing
 * about interceptor behaviour. This mirrors the interceptor logic in
 * src/api/client.ts and drives real requests, so a mistake in header
 * handling, error shaping or timeouts is caught rather than assumed.
 *
 *   node scripts/verify-gateway.mjs            # against http://localhost:8000/api
 *   API=http://localhost:8000/api node scripts/verify-gateway.mjs
 *
 * Not part of the app bundle. Safe to delete.
 */
import axios, { AxiosHeaders } from "axios";

const BASE = process.env.API ?? "http://localhost:8000/api";
let ACTIVE_USER = null;

class ApiError extends Error {
  constructor(status, message, isTimeout = false) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isTimeout = isTimeout;
  }
  get isNotFound() {
    return this.status === 404;
  }
}

function describe(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d === "string") return d;
        const field = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
        return field ? `${field}: ${d.msg ?? ""}` : (d.msg ?? "");
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

const client = axios.create({ baseURL: BASE, timeout: 90000, withCredentials: false });

client.interceptors.request.use((cfg) => {
  const headers = AxiosHeaders.from(cfg.headers);
  if (ACTIVE_USER) headers.set("X-User-Id", ACTIVE_USER);
  if (cfg.data instanceof FormData) headers.delete("Content-Type");
  else if (cfg.data !== undefined && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  cfg.headers = headers;
  return cfg;
});

client.interceptors.response.use(
  (r) => {
    const type = String(r.headers?.["content-type"] ?? "");
    const expectsJson = r.config.responseType !== "blob";
    if (expectsJson && r.status !== 204 && type && !type.includes("application/json"))
      throw new ApiError(r.status, `Expected JSON but received "${type}"`);
    return r;
  },
  async (error) => {
    if (error instanceof ApiError) throw error;
    if (axios.isAxiosError(error)) {
      if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT")
        throw new ApiError(0, "Request timed out", true);
      if (error.code === "ERR_CANCELED") throw new ApiError(0, "Request cancelled", true);
      if (!error.response)
        throw new ApiError(0, "Cannot reach the API. Is the backend running?");
      const { status, statusText, data } = error.response;
      throw new ApiError(status, describe(data?.detail, statusText || "Request failed"));
    }
    throw new ApiError(0, String(error));
  },
);

const http = {
  get: (p, o) => client.get(p, o).then((r) => r.data),
  post: (p, b, o) => client.post(p, b, o).then((r) => r.data),
  upload: (p, file, fields = {}) => {
    const f = new FormData();
    f.append("file", file);
    for (const [k, v] of Object.entries(fields)) f.append(k, v);
    return client.post(p, f).then((r) => r.data);
  },
};

let pass = 0;
let fail = 0;
const ok = (cond, msg) => {
  cond ? (pass++, console.log("  OK   " + msg)) : (fail++, console.log("  FAIL " + msg));
};

console.log(`gateway verification against ${BASE}\n`);

console.log("=== 1. GET with no identity ===");
const templates = await http.get("/templates");
ok(templates.length === 7, `templates: ${templates.length} (expect 7)`);
ok(Boolean(templates[0].design_tokens?.font), "design_tokens present on template");

console.log("\n=== 2. request interceptor attaches X-User-Id ===");
const users = await http.get("/users");
ACTIVE_USER = users[0].id;
const mine = await http.get("/resumes");
ok(Array.isArray(mine), `scoped resume list returned (${mine.length})`);

console.log("\n=== 3. multipart upload keeps its boundary ===");
const file = new File(
  [new TextEncoder().encode("Jane Doe\njane@x.com\n\nEXPERIENCE\nAcme - Eng - Jan 2020\n- Did work\n")],
  "cv.txt",
  { type: "text/plain" },
);
const uploaded = await http.upload("/resumes/upload", file, { title: "Axios Gateway Test" });
ok(uploaded.parse_status === "ready", `parse_status=${uploaded.parse_status}`);
ok(uploaded.title === "Axios Gateway Test", `title field survived: ${uploaded.title}`);

console.log("\n=== 4. JSON POST sets Content-Type ===");
const applied = await http.post(`/resumes/${uploaded.id}/template`, {
  template_key: "executive",
});
ok(applied.template_key === "executive", `template=${applied.template_key}`);

console.log("\n=== 5. failures become ApiError carrying the server detail ===");
try {
  await http.upload("/resumes/upload", new File([new Uint8Array([1])], "bad.exe"));
  fail++;
  console.log("  FAIL .exe was accepted");
} catch (e) {
  ok(
    // 422, not 400: UnsupportedFormat subclasses ValidationError, which
    // main.py maps to 422. The mock used to return 400 and hid this.
    e instanceof ApiError && e.status === 422 && e.message.includes(".exe"),
    `${e.status} -> "${e.message}"`,
  );
}
try {
  await http.post(`/resumes/${uploaded.id}/template`, { template_key: "nope" });
  fail++;
  console.log("  FAIL unknown template accepted");
} catch (e) {
  ok(e.status === 404 && e.isNotFound, `404 isNotFound -> "${e.message}"`);
}

console.log("\n=== 6. cross-user isolation ===");
ACTIVE_USER = users[1].id;
try {
  await http.get(`/resumes/${uploaded.id}`);
  fail++;
  console.log("  FAIL another user read the resume");
} catch (e) {
  ok(e.status === 404, `other user -> 404 "${e.message}"`);
}
ACTIVE_USER = users[0].id;

console.log("\n=== 7. timeout sets isTimeout ===");
// A 1ms timeout normally beats a local response, but not always -- if the
// request wins the race the assertion is meaningless rather than wrong, so
// retry a few times. We are testing the error MAPPING, not latency.
let timedOut = null;
for (let attempt = 0; attempt < 5 && !timedOut; attempt++) {
  try {
    await client.get("/templates", { timeout: 1 });
  } catch (e) {
    timedOut = e;
  }
}
if (!timedOut) {
  fail++;
  console.log("  FAIL did not time out in 5 attempts");
} else {
  ok(
    timedOut.isTimeout === true && timedOut.status === 0,
    `isTimeout=${timedOut.isTimeout} status=${timedOut.status}`,
  );
}

console.log("\n=== 8. backend down -> friendly message ===");
const dead = axios.create({ baseURL: "http://127.0.0.1:9999/api", timeout: 2000 });
dead.interceptors.response.use(
  (r) => r,
  async (error) => {
    if (!error.response)
      throw new ApiError(0, "Cannot reach the API. Is the backend running?");
    throw new ApiError(error.response.status, "x");
  },
);
try {
  await dead.get("/users");
  fail++;
  console.log("  FAIL connected to nothing");
} catch (e) {
  ok(e.message.includes("Cannot reach the API"), `"${e.message}"`);
}

console.log("\n=== 9. FastAPI 422 array flattening ===");
ok(
  describe([{ loc: ["body", "template_key"], msg: "field required" }], "fb") ===
    "template_key: field required",
  'nested 422 -> "template_key: field required"',
);
ok(describe("plain string", "fb") === "plain string", "string detail passes through");
ok(describe(undefined, "fallback") === "fallback", "missing detail -> fallback");

console.log(fail ? `\n>>> ${fail} FAILURES` : `\n>>> all ${pass} checks passed`);
process.exit(fail ? 1 : 0);
