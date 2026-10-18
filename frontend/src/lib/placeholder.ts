/**
 * The metric placeholder, mirrored from the backend.
 *
 * Source of truth is `backend/app/services/placeholders.py` — this is the
 * second spelling of the same literal, which is exactly the kind of pair
 * that drifts. `scripts/test-change-refs.mjs` reads the Python file and
 * fails if the two disagree, so the duplication cannot rot silently.
 *
 * Why a placeholder exists at all: `summary.no_metric` and
 * `experience.unquantified_bullets` are cleared only by a number, and the
 * writer is forbidden from inventing one. So it restructures the line so a
 * figure fits and leaves this blank for the candidate, which asserts
 * nothing and therefore cannot fabricate anything.
 */
export const METRIC_PLACEHOLDER = "[__]";
