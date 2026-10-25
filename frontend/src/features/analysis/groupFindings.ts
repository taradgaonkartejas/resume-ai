import { rankFindings, type Finding, type StructuredData } from "@/services";

/**
 * Grouping logic for findings that repeat per entry, kept separate from the
 * component that renders them so it can be exercised without a DOM.
 *
 * A flat list works for Contact (one subject), Summary (one subject) and
 * Skills (one subject). It stops working for Experience and Projects, where
 * "weak verb" can appear once per entry and the only way to tell them apart
 * is to read the target_ref. The user has to hold "which entry was that one
 * about?" in their head for every card.
 *
 * Grouping is derived entirely from `target_ref` (`exp_0.bullet_3` -> role 0,
 * `prj_1.bullet_0` -> project 1) and the resume data already in the editor,
 * so this needs no backend change.
 *
 * Findings that are not about a specific entry -- anything whose ref does not
 * resolve to an entry of `section` -- are kept in an ungrouped list rather
 * than being dropped. A finding the user cannot see is a point they cannot win.
 */

/** `exp_2.bullet_1` / `prj_2.bullet_1` -> 2. Null for anything else. */
export function entryIndexOf(targetRef: string, prefix: "exp" | "prj"): number | null {
  const m = new RegExp(`^${prefix}_(\\d+)\\b`).exec(targetRef);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/** Backward-compatible alias: `exp_N` index or null. */
export function roleIndexOf(targetRef: string): number | null {
  return entryIndexOf(targetRef, "exp");
}

export type EntryGroupData = {
  key: string;
  title: string;
  subtitle: string;
  findings: Finding[];
};

export type SectionKind = "experience" | "projects";

export function groupFindingsByEntry(
  findings: Finding[],
  data: StructuredData | undefined,
  section: SectionKind,
): { groups: EntryGroupData[]; ungrouped: Finding[] } {
  const prefix = section === "projects" ? "prj" : "exp";
  const entries = section === "projects" ? data?.projects ?? [] : data?.experience ?? [];
  const byEntry = new Map<number, Finding[]>();
  const ungrouped: Finding[] = [];

  for (const f of findings) {
    const idx = entryIndexOf(f.target_ref, prefix);
    // An index past the end of the array means the ref points at an entry
    // that no longer exists -- the user deleted it after the analysis ran.
    // Surface it ungrouped rather than inventing a heading for it.
    if (idx === null || idx >= entries.length) {
      ungrouped.push(f);
      continue;
    }
    const list = byEntry.get(idx) ?? [];
    list.push(f);
    byEntry.set(idx, list);
  }

  const groups: EntryGroupData[] = [...byEntry.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([idx, list]) => {
      // Entries are a projects/experience union at this point; the heading
      // only ever reads a handful of optional string fields, so address it
      // through a structural shape instead of {@code {}} — indexing the raw
      // union with a fallback produced "property does not exist on {}".
      const entry: {
        name?: string | null;
        role?: string | null;
        company?: string | null;
        dates?: string | null;
      } = entries[idx] ?? {};
      const key = `${prefix}_${idx}`;

      if (section === "projects") {
        const name = (entry.name ?? "").trim();
        return {
          key,
          title: name || `Project ${idx + 1}`,
          subtitle: "",
          findings: rankFindings(list),
        };
      }

      const role = (entry.role ?? "").trim();
      const company = (entry.company ?? "").trim();
      const dates = (entry.dates ?? "").trim();

      // The title already absorbs role and company in every branch, so the
      // subtitle is just the dates. An earlier version prefixed the company
      // here too and printed it twice under "Role at Company".
      const title =
        role && company
          ? `${role} at ${company}`
          : role || company || `Role ${idx + 1}`;
      const subtitle = dates;

      return { key, title, subtitle, findings: rankFindings(list) };
    });

  return { groups, ungrouped };
}

/** Convenience wrapper for the experience step. */
export function groupFindingsByRole(
  findings: Finding[],
  data: StructuredData | undefined,
): { groups: EntryGroupData[]; ungrouped: Finding[] } {
  return groupFindingsByEntry(findings, data, "experience");
}
