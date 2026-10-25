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
import { dateLabel } from "@/lib/dates";

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
export function educationLines(
  entry: EducationEntry,
  schoolFirst = false,
): [string, string] {
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
    // a blank line with a date beside it. Same in both orders.
    primary = school;
    school = "";
  }

  if (schoolFirst && school) {
    // Fix-list defect 9, executive reference: the bold dates row carries
    // "School, City"; the degree line drops beneath and keeps the grade.
    const headline = withLocation(school, entry.location);
    let detail = primary;
    if (grade) detail = detail ? `${detail} · ${grade}` : grade;
    return [headline, detail];
  }

  let detail = withLocation(school, entry.location);
  if (grade) detail = detail ? `${detail} · ${grade}` : grade;
  return [primary, detail];
}

/**
 * [primary, detail, datesRight] for one education entry.
 *
 * Mirror of `resume_ops.education_cells`. The inline form folds the school
 * into the degree row ("PG-DAC — CDAC") and moves the location next to the
 * dates on the right ("2024 | Pune"), leaving grade as the detail row.
 */
export function educationCells(
  entry: EducationEntry,
  inline = false,
  /** Stacked-only, mirroring the Python: an inline row does not reverse. */
  schoolFirst = false,
): [string, string, string] {
  const [primary0, detail0] = educationLines(entry, schoolFirst && !inline);
  const dates = dateLabel(entry);
  if (!inline) return [primary0, detail0, dates];

  const location = (entry.location ?? "").trim();
  const grade = (entry.grade ?? "").trim();
  const rawSchool = (entry.school ?? "").trim();
  const school = withLocation(entry.school ?? "", location);
  // educationLines promotes the school to primary when no degree exists;
  // gluing it on again would print it twice.
  const primary =
    school && !primary0.includes(rawSchool)
      ? primary0
        ? `${primary0} — ${school}`
        : school
      : primary0;
  const right = dates && location ? `${dates} | ${location}` : dates;
  return [primary, grade, right];
}

/** [primary, detail] for one experience entry. `location` was dropped too. */
export function experienceLines(
  entry: ExperienceEntry,
  inline = false,
  /** Which line carries the dates. Mirrors experience_lines() in
   *  resume_ops.py -- the preview must predict the PDF. */
  lead: "role" | "company" = "role",
  /** Separator inside an inline row. The template's `entry_sep` must come
   *  through here -- a preview that folds with " · " while the PDF folds
   *  with " - " (or banner's "  |  ") is exactly the drift this mirror
   *  exists to prevent. */
  sep = " - ",
): [string, string] {
  const role = (entry.role ?? "").trim();
  const company = withLocation(entry.company, entry.location);
  if (inline) return [[role, company].filter(Boolean).join(sep), ""];
  if (lead === "company" && company) return [company, role];
  if (role) return [role, company];
  return [company, ""];
}
