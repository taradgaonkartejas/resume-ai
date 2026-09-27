/**
 * Service layer — one module per backend router, covering all 28 operations.
 *
 * Layering, deliberately strict:
 *
 *   components/         render, and call hooks
 *        |
 *   api/queries.ts      caching + invalidation (TanStack Query)
 *        |
 *   services/           endpoint paths and payload shapes   <- you are here
 *        |
 *   api/client.ts       HTTP: base URL, X-User-Id, timeout, errors
 *
 * A service never imports React and never caches. It is a typed description
 * of the backend and nothing more, so it stays testable and a route change
 * has exactly one place to land.
 */

export { userService } from "./userService";
export { templateService } from "./templateService";
export { resumeService } from "./resumeService";
export {
  analysisService, pointsTo80, totalFindings, rankFindings,
} from "./analysisService";
export { tailorService, keywordProgress } from "./tailorService";
export { suggestionService, effectiveText } from "./suggestionService";
export { chatService } from "./chatService";
export { versionService } from "./versionService";
export { exportService } from "./exportService";
export { systemService } from "./systemService";

export * from "./types";
