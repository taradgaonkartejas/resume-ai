import { rankFindings, type Finding, type StructuredData } from "@/services";

/**
 * Grouping logic for the experience step's findings, kept separate from the
 * component that renders them so it can be exercised without a DOM.
 *
 * A flat list works for Contact (one subject) and Summary (one subject). It
 * stops working for Experience, where "Weak verb" can appear five times, each
 * about a different job, and the only way to tell them apart is to read the
 * target_ref. The user has to hold "which role was that one about?" in their
 * head for every card.
 *
 * Grouping is derived entirely from `target_ref` (`exp_0.bullet_3` -> role 0)
 * and the resume data already in the editor, so this needs no backend change.
 *
 * Findings that are not about a specific role -- anything whose ref does not
 * resolve to an experience entry -- are kept in an ungrouped list rather than
 * being dropped. A finding the user cannot see is a point they cannot win.
 */

/** `exp_2.bullet_1` -> 2. Null for anything that is not a role-scoped ref. */
export function roleIndexOf(targetRef: string): number | null {
  const m = /^exp_(\d+)\b/.exec(targetRef);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

export type RoleGroupData = {
  key: string;
  title: string;
  subtitle: string;
  findings: Finding[];
};

export function groupFindingsByRole(
  findings: Finding[],
  data: StructuredData | undefined,
): { groups: RoleGroupData[]; ungrouped: Finding[] } {
  const experience = data?.experience ?? [];
  const byRole = new Map<number, Finding[]>();
  const ungrouped: Finding[] = [];

  for (const f of findings) {
    const idx = roleIndexOf(f.target_ref);
    // An index past the end of the array means the ref points at a role that
    // no longer exists -- the user deleted a job after the analysis ran.
    // Surface it ungrouped rather than inventing a heading for it.
    if (idx === null || idx >= experience.length) {
      ungrouped.push(f);
      continue;
    }
    const list = byRole.get(idx) ?? [];
    list.push(f);
    byRole.set(idx, list);
  }

  const groups: RoleGroupData[] = [...byRole.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([idx, list]) => {
      const entry = experience[idx];
      const role = (entry?.role ?? "").trim();
      const company = (entry?.company ?? "").trim();
      const dates = (entry?.dates ?? "").trim();

      // The title already absorbs role and company in every branch, so the
      // subtitle is just the dates. An earlier version prefixed the company
      // here too and printed it twice under "Role at Company".
      const title =
        role && company
          ? `${role} at ${company}`
          : role || company || `Role ${idx + 1}`;
      const subtitle = dates;

      return { key: `exp_${idx}`, title, subtitle, findings: rankFindings(list) };
    });

  return { groups, ungrouped };
}
