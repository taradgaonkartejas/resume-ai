import type { EducationEntry, ExperienceEntry, ProjectEntry } from "@/services";

type Dated = Partial<
  Pick<
    ExperienceEntry & EducationEntry & ProjectEntry,
    "start_date" | "end_date" | "current" | "raw_dates" | "dates"
  >
>;

/**
 * The display string for one entry's date range.
 *
 * A deliberate mirror of `resume_ops.date_label()` in the backend. Both exist
 * because the preview must render the user's *unsaved* draft — there is no
 * round-trip to recompute the derived `dates` mirror mid-keystroke, so the
 * client needs the same rule.
 *
 * Kept in sync by `tests/test_date_migration.py::test_date_label` on the
 * server and `dates.test.ts` here, which assert the same table of cases.
 */
export function dateLabel(entry: Dated | null | undefined): string {
  if (!entry) return "";

  // An ambiguous legacy string is shown exactly as it was written, never
  // reinterpreted — a resume that misstates employment dates is far worse
  // than one that shows an odd string.
  const raw = (entry.raw_dates ?? "").trim();
  if (raw) return raw;

  const start = (entry.start_date ?? "").trim();
  const end = (entry.end_date ?? "").trim();

  if (entry.current) return start ? `${start} – Present` : "Present";
  if (start && end) return `${start} – ${end}`;
  if (start || end) return start || end;

  // Un-migrated legacy document.
  return (entry.dates ?? "").trim();
}

/** Years of experience implied by the entries, for the tenure hint. */
export function totalYears(entries: ExperienceEntry[]): number | null {
  const years = entries
    .map((e) => {
      const start = parseYear(e.start_date);
      if (start === null) return null;
      const end = e.current ? new Date().getFullYear() : parseYear(e.end_date);
      if (end === null) return null;
      return Math.max(0, end - start);
    })
    .filter((n): n is number => n !== null);
  if (years.length === 0) return null;
  return years.reduce((a, b) => a + b, 0);
}

function parseYear(value: string | undefined): number | null {
  const match = (value ?? "").match(/\b(19|20)\d{2}\b/);
  return match ? Number(match[0]) : null;
}
