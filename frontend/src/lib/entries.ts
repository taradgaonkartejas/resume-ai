/**
 * Entry formatters shared by the preview and the exporters.
 *
 * Mirrors `with_location` / `education_lines` / `experience_lines` in
 * `backend/app/services/resume_ops.py`. The preview exists to show what the
 * PDF will contain, so the two must compose entries identically — these are
 * pinned by `backend/tests/test_entry_lines.py`, which reads the case table
 * out of this file.
 */
import type { EducationEntry, ExperienceEntry } from "@/services/types";

/**
 * "Acme Corp" + "Pune" -> "Acme Corp, Pune".
 *
 * Suppresses a location already inside the name: a school parsed as
 * "CDAC, Pune" carrying location "Pune" must not render "CDAC, Pune, Pune",
 * and that is the common case rather than a corner one.
 */
export function withLocation(name: string | undefined, location: string | undefined): string {
  const n = (name ?? "").trim();
  const place = (location ?? "").trim();
  if (!place) return n;
  if (!n) return place;
  if (n.toLowerCase().includes(place.toLowerCase())) return n;
  return `${n}, ${place}`;
}

/** [primary, detail] for one education entry. */
export function educationLines(entry: EducationEntry): [string, string] {
  const degree = (entry.degree ?? "").trim();
  const field = (entry.field_of_study ?? "").trim();
  let school = (entry.school ?? "").trim();
  const grade = (entry.grade ?? "").trim();

  let primary = degree;
  if (field && !degree.toLowerCase().includes(field.toLowerCase())) {
    primary = degree ? `${degree}, ${field}` : field;
  }
  if (!primary) {
    // No degree at all: the school becomes the headline so the entry is never
    // a blank line with a date beside it.
    primary = school;
    school = "";
  }

  let detail = withLocation(school, entry.location);
  if (grade) detail = detail ? `${detail} · ${grade}` : grade;
  return [primary, detail];
}

/** [primary, detail] for one experience entry. `location` was dropped too. */
export function experienceLines(
  entry: ExperienceEntry,
  inline = false,
): [string, string] {
  const role = (entry.role ?? "").trim();
  const company = withLocation(entry.company, entry.location);
  if (inline) return [[role, company].filter(Boolean).join(" · "), ""];
  if (role) return [role, company];
  return [company, ""];
}
