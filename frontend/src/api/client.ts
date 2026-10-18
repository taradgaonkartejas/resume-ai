import axios, {
  AxiosError,
  AxiosHeaders,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";
import { env } from "@/lib/env";

/**
 * The API gateway. Every request in the app goes through this one axios
 * instance.
 *
 * Why axios rather than raw fetch:
 *   - interceptors give ONE place for auth identity and error shaping, so a
 *     service never touches headers or unwraps a response
 *   - a real timeout option instead of hand-rolled AbortSignal plumbing
 *   - it rejects on 4xx/5xx, so a forgotten `res.ok` check cannot silently
 *     hand a component an error body as if it were data
 *
 * The exported `http` surface is unchanged from the fetch version, so the ten
 * modules in services/ did not need a single edit.
 */

const USER_KEY = "resumeai.activeUserId";

export function getActiveUserId(): string | null {
  return localStorage.getItem(USER_KEY);
}

export function setActiveUserId(id: string): void {
  localStorage.setItem(USER_KEY, id);
}

export class ApiError extends Error {
  readonly status: number;
  /** True when the request never got a response: timeout, abort, or the
   *  backend being down. */
  readonly isTimeout: boolean;
  /** Machine-readable cause from the response body, when the backend sent
   *  one. Only 422s from /rewrite carry it today. Prose is for the user;
   *  this is for the code, which must not grep a sentence to decide what to
   *  offer next. */
  readonly reason?: string;

  constructor(status: number, message: string, isTimeout = false, reason?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.isTimeout = isTimeout;
    this.reason = reason;
  }

  /** The failure is upstream and transient, so retrying is meaningful.
   *
   *  Worth distinguishing in the UI: a 422 means "what you asked for cannot be
   *  done" and a red, final-looking message is right. A 503 means "we could
   *  not reach the model just now", which is not the user's fault and not a
   *  dead end -- painting it the same red tells them they were refused. */
  get isTransient(): boolean {
    return this.isTimeout || this.status === 503 || this.status === 429;
  }

  /** Nothing the user can do *in this session* will change this answer, so
   *  do not offer a retry.
   *
   *  `needs_your_fact` -- a model read the line and is refusing to invent a
   *  number it cannot find in the resume. A rule, not a hiccup; asking again
   *  just replays two model calls to reach the same 422.
   *
   *  `writer_not_configured` -- no AI writer exists, so only the rule engine
   *  ran and it had no mechanical fix. Equally permanent until a key is
   *  added, and it MUST be listed here: a new reason that matched no branch
   *  would quietly fall back to the retryable path and offer a button that
   *  can never succeed. */
  get isFinalRefusal(): boolean {
    return (
      this.reason === "needs_your_fact" || this.reason === "writer_not_configured"
    );
  }

  /** The fact-checker threw the draft away.
   *
   *  Deliberately NOT part of `isFinalRefusal`, though it was tempting. The
   *  writer runs at temperature 0.4, so a retry genuinely can produce a draft
   *  that passes -- unlike `needs_your_fact`, where the model has reasoned
   *  that the number cannot be known and will reason that way again.
   *
   *  It is its own state because it is also not an *error*. Nothing is
   *  broken and the user did nothing wrong: the safety net caught a rewrite
   *  that would have put a fabricated claim on a resume. Showing it in the
   *  same red as a failure teaches the user to distrust the tool at the
   *  moment it is protecting them. The caller should say what was objected
   *  to -- `message` already carries the reviewer's own words -- and cap the
   *  retries, because a writer that invented a detail once tends to reach
   *  for it again. */
  get isReviewerObjection(): boolean {
    return this.reason === "critic_rejected";
  }

  /** Nothing to rewrite -- the rule engine found no mechanical fix.
   *
   *  Listed so that every `reason` the backend can emit is named somewhere
   *  in this file. `scripts/test-error-reasons.mjs` enforces that, because
   *  the comment on `isFinalRefusal` warned a stray reason would fall
   *  through to the retryable path, and `critic_rejected` then did exactly
   *  that. A warning in a comment is not a guard. */
  get isNothingToRewrite(): boolean {
    return this.reason === "nothing_to_rewrite";
  }

  /** The backend returns 404 both for a missing resume and for one owned by
   *  another user — deliberately indistinguishable. */
  get isNotFound() {
    return this.status === 404;
  }
}

/**
 * FastAPI returns {detail: string} for HTTPException but {detail: [{loc, msg}]}
 * for 422 validation errors. Flatten both to one readable line, otherwise a
 * 422 surfaces in the UI as "[object Object]".
 */
function describe(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d === "string") return d;
        const item = d as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
        return field ? `${field}: ${item.msg ?? ""}` : (item.msg ?? "");
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

/**
 * Error bodies from a responseType:"blob" request arrive as a Blob, not JSON,
 * so `data.detail` is undefined and the user would see a bare status code.
 * Read the blob back into JSON to recover the real message.
 */
async function detailFromBlob(data: Blob): Promise<unknown> {
  try {
    return JSON.parse(await data.text())?.detail;
  } catch {
    return undefined;
  }
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: env.apiBaseUrl,
  timeout: env.apiTimeoutMs,
  // Never send cookies: identity is the X-User-Id header, and credentialed
  // requests would force stricter CORS for no benefit.
  withCredentials: false,
});

/* ---------- request: attach identity ---------- */

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const headers = AxiosHeaders.from(config.headers);

  const userId = getActiveUserId();
  if (userId) headers.set("X-User-Id", userId);

  // axios sets multipart/form-data WITH the generated boundary only if the
  // header is absent. Any value we set here strips the boundary and the
  // server fails to parse the upload.
  if (config.data instanceof FormData) {
    headers.delete("Content-Type");
  } else if (config.data !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  config.headers = headers;
  return config;
});

/* ---------- response: normalise every failure to ApiError ---------- */

apiClient.interceptors.response.use(
  (response) => {
    // A proxy returning an HTML error page with status 200 would otherwise
    // reach a component as a string and fail far from its cause.
    const type = String(response.headers?.["content-type"] ?? "");
    const expectsJson = response.config.responseType !== "blob";
    if (expectsJson && response.status !== 204 && type && !type.includes("application/json")) {
      throw new ApiError(response.status, `Expected JSON but received "${type}"`);
    }
    return response;
  },
  async (error: unknown) => {
    if (error instanceof ApiError) throw error;

    if (axios.isAxiosError(error)) {
      const err = error as AxiosError;

      if (err.code === "ECONNABORTED" || err.code === "ETIMEDOUT") {
        throw new ApiError(0, `Request timed out after ${env.apiTimeoutMs} ms`, true);
      }
      if (err.code === "ERR_CANCELED") {
        throw new ApiError(0, "Request cancelled", true);
      }
      if (!err.response) {
        throw new ApiError(0, "Cannot reach the API. Is the backend running?");
      }

      const { status, statusText, data } = err.response;
      const body = data instanceof Blob ? undefined : (data as { detail?: unknown; reason?: unknown });
      const detail = data instanceof Blob ? await detailFromBlob(data) : body?.detail;
      const reason = typeof body?.reason === "string" ? body.reason : undefined;

      throw new ApiError(status, describe(detail, statusText || "Request failed"), false, reason);
    }

    throw new ApiError(0, error instanceof Error ? error.message : "Unknown error");
  },
);

/* ---------- public surface ---------- */

export interface RequestOptions
  extends Omit<AxiosRequestConfig, "url" | "method" | "data" | "params"> {
  /** Override the instance timeout for one call. */
  timeoutMs?: number;
  /** Query params; undefined/null entries are dropped before sending. */
  query?: Record<string, string | number | boolean | undefined | null>;
}

function toConfig(options: RequestOptions = {}): AxiosRequestConfig {
  const { timeoutMs, query, ...rest } = options;
  const params = query
    ? Object.fromEntries(
        Object.entries(query).filter(([, v]) => v !== undefined && v !== null),
      )
    : undefined;
  return { ...rest, params, ...(timeoutMs ? { timeout: timeoutMs } : {}) };
}

export const http = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiClient.get<T>(path, toConfig(options)).then((r) => r.data),

  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiClient.post<T>(path, body, toConfig(options)).then((r) => r.data),

  put: <T>(path: string, body: unknown, options?: RequestOptions) =>
    apiClient.put<T>(path, body, toConfig(options)).then((r) => r.data),

  patch: <T>(path: string, body: unknown, options?: RequestOptions) =>
    apiClient.patch<T>(path, body, toConfig(options)).then((r) => r.data),

  delete: <T>(path: string, options?: RequestOptions) =>
    apiClient.delete<T>(path, toConfig(options)).then((r) => r.data),

  /** Multipart upload. Content-Type is left to axios — see the interceptor. */
  upload: <T>(
    path: string,
    file: File,
    fields: Record<string, string> = {},
    options?: RequestOptions,
  ) => {
    const form = new FormData();
    form.append("file", file);
    for (const [k, v] of Object.entries(fields)) form.append(k, v);
    return apiClient.post<T>(path, form, toConfig(options)).then((r) => r.data);
  },

  /** Binary GET. Exports stream bytes rather than returning a presigned URL,
   *  because presigned URLs do not exist in disk-fallback storage mode. */
  blob: async (
    path: string,
    options?: RequestOptions,
  ): Promise<{ blob: Blob; filename: string | null }> => {
    const response = await apiClient.get<Blob>(path, {
      ...toConfig(options),
      responseType: "blob",
    });

    // Prefer the server's filename: the exporter derives it from the resume
    // title and reduces it to an ASCII slug, since headers are latin-1 only.
    const disposition = String(response.headers?.["content-disposition"] ?? "");
    const match = /filename="?([^"]+)"?/.exec(disposition);
    return { blob: response.data, filename: match?.[1] ?? null };
  },
};

/** Kept for existing imports. */
export const api = http;
