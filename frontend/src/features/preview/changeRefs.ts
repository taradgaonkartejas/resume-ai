/**
 * Target refs, as the backend's `resume_ops` spells them.
 *
 * The preview highlights "what just changed", and a changed range is
 * identified by the same `target_ref` the suggestion carried. That makes the
 * exact spelling load-bearing: if the UI builds `experience.0.bullets.0` and
 * the resolver expects `exp_0.bullet_0`, nothing matches and the highlight
 * silently never appears.
 *
 * That is not hypothetical. The seeded demo data shipped with exactly that
 * mistake -- a `target_ref` the resolver rejects -- and neither the type
 * checker nor the test suite caught it, because both halves were internally
 * consistent and simply never compared. Hence this module: one place that
 * spells refs, used by the preview and pinned by tests.
 *
 * The four forms `resume_ops` understands:
 *
 *     summary.text        exp_N.bullet_M      prj_N.bullet_M      skills.N.items
 *
 * `edu_N.field` is NOT in that list yet -- education addressing is its own
 * increment. The builder exists here so the preview needs no change when it
 * lands, but nothing emits one today.
 */

export const SUMMARY_REF = "summary.text";

export function experienceBulletRef(entry: number, bullet: number): string {
  return `exp_${entry}.bullet_${bullet}`;
}

export function projectBulletRef(entry: number, bullet: number): string {
  return `prj_${entry}.bullet_${bullet}`;
}

export function skillsRef(group: number): string {
  return `skills.${group}.items`;
}

/**
 * Education fields a suggestion may address.
 *
 * Mirrors `_EDU_FIELDS` in resume_ops.py exactly. `dates` is NOT in the
 * list: education dates are stored twice (a display string and a structured
 * pair) and patching one without the other desynchronises them.
 */
export type EducationField =
  | "school"
  | "degree"
  | "field_of_study"
  | "location"
  | "grade";

export const EDUCATION_FIELDS: readonly EducationField[] = [
  "school",
  "degree",
  "field_of_study",
  "location",
  "grade",
];

export function educationRef(entry: number, field: EducationField): string {
  return `edu_${entry}.${field}`;
}

/**
 * Changed refs as a lookup.
 *
 * A plain array lives in the reducer (serialisable, cheap to compare); the
 * Set is derived at render time where membership is actually tested.
 */
export function changedRefSet(refs: readonly string[]): ReadonlySet<string> {
  return new Set(refs);
}

/**
 * Should this ref be marked?
 *
 * `show` is threaded through rather than checked by the caller so there is a
 * single place where "the toggle is off" wins -- a highlight that survives
 * the toggle is worse than no highlight at all.
 */
export function isChanged(
  ref: string,
  changed: ReadonlySet<string>,
  show: boolean,
): boolean {
  return show && changed.has(ref);
}
