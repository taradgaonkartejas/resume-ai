/**
 * Environment access, in one place.
 *
 * Every read of import.meta.env happens here so that:
 *   - a rename is one edit, not a grep across the app
 *   - values are validated once, at module load, rather than at each use
 *   - the rest of the codebase imports plain typed constants
 *
 * Vite inlines VITE_* at BUILD time. Changing .env.local requires a dev-server
 * restart; it is not read at runtime.
 */

function readBaseUrl(): string {
  const raw = (import.meta.env.VITE_API_BASE_URL ?? "").trim();

  // Empty is the intended default: same-origin "/api", proxied by Vite in dev
  // and served by the same host in production.
  if (!raw) return "/api";

  // Strip trailing slashes so joining never produces "//resumes".
  const trimmed = raw.replace(/\/+$/, "");

  // A base URL without /api is the easy misconfiguration: every request would
  // 404 with no clue why. Warn loudly rather than failing silently.
  if (!/\/api$/.test(trimmed) && /^https?:\/\//.test(trimmed)) {
    console.warn(
      `[env] VITE_API_BASE_URL="${raw}" does not end in /api. ` +
        `Backend routes are mounted under /api, so requests will 404. ` +
        `Did you mean "${trimmed}/api"?`,
    );
  }
  return trimmed;
}

function readTimeout(): number {
  const raw = import.meta.env.VITE_API_TIMEOUT_MS;
  const parsed = Number(raw);
  if (raw !== undefined && raw !== "" && !Number.isFinite(parsed)) {
    console.warn(`[env] VITE_API_TIMEOUT_MS="${raw}" is not a number. Using 90000.`);
  }
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 90_000;
}

/**
 * Budget for requests that run the LLM chain, as opposed to a plain read.
 *
 * These two numbers were independent and that was the bug. The server tries a
 * CHAIN of models and spends up to `LLM_TIMEOUT_SECONDS` on each, so its worst
 * case is `timeout x chain length` -- currently 45s x 4 = 180s for `writing`.
 * The browser gave up at 90s, which is less than ONE attempt when a stale
 * .env set LLM_TIMEOUT_SECONDS=90. The user saw "Request timed out after
 * 90000 ms" while the backend was still working, and the models that would
 * have answered were never reached.
 *
 * So this is deliberately larger than the server's worst case rather than a
 * round number: a client that aborts mid-chain converts a slow success into a
 * failure, and retrying costs the whole chain again.
 *
 * It is NOT the default for every request -- a hung GET should fail fast, not
 * hang a screen for four minutes. Only the LLM-backed mutations opt in.
 */
export const LLM_REQUEST_TIMEOUT_MS = 240_000;

export const env = {
  /** "/api" (same origin) or an absolute origin ending in /api. */
  apiBaseUrl: readBaseUrl(),
  /** Per-request abort timeout in ms. */
  apiTimeoutMs: readTimeout(),
  isDev: import.meta.env.DEV,
} as const;
