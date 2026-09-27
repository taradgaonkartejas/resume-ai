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

export const env = {
  /** "/api" (same origin) or an absolute origin ending in /api. */
  apiBaseUrl: readBaseUrl(),
  /** Per-request abort timeout in ms. */
  apiTimeoutMs: readTimeout(),
  isDev: import.meta.env.DEV,
} as const;
