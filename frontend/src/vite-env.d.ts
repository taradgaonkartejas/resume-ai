/// <reference types="vite/client" />

/**
 * Types for the VITE_* variables in .env.example.
 *
 * Without this, import.meta.env.VITE_ANYTHING is `any` and a typo fails
 * silently at runtime as `undefined`. Declaring them makes an unknown name a
 * compile error instead.
 */
interface ImportMetaEnv {
  /** Absolute API origin + /api. Empty means "same origin", which is the
   *  normal dev setup via the Vite proxy. */
  readonly VITE_API_BASE_URL?: string;
  /** Per-request timeout in ms. Parsed with a fallback; see lib/env.ts. */
  readonly VITE_API_TIMEOUT_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
