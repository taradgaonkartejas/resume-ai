import type { SuggestionOut } from "@/services";

/**
 * Grouping for the Tailor suggestion list, kept out of the component so it
 * can be exercised without a DOM (same arrangement as groupFindings.ts).
 *
 * A flat list of six cards gives the user no way to see WHERE the work is.
 * "Rewrite this bullet" five times in a row reads as five unrelated chores;
 * the same five under "Experience · 5" read as one pass over one section,
 * and the user can decide to skip a whole section at a glance.
 *
 * The section comes from the server's `section` field, which the draft
 * builders already set to a clean key. `target_ref` is the fallback, not the
 * primary: the two can disagree only if the backend changes, and when they
 * do the ref is the thing that actually gets written to.
 */

/** Canonical section keys, in the order a resume reads top to bottom. */
const ORDER = ["summary", "experience", "projects", "education", "skills"] as const;

export type SectionKey = (typeof ORDER)[number] | "other";

const TITLES: Record<SectionKey, string> = {
  summary: "Professional summary",
  experience: "Experience",
  projects: "Projects",
  education: "Education",
  skills: "Skills",
  other: "Other",
};

/**
 * `exp_0.bullet_2` -> "experience".
 *
 * Education is listed even though nothing targets it yet: `resume_ops` has no
 * education ref, so the backend cannot produce one. Leaving the slot in place
 * means that when it can, the group appears in the right position with no
 * change here.
 */
export function sectionOfRef(targetRef: string): SectionKey {
  if (targetRef.startsWith("summary")) return "summary";
  if (targetRef.startsWith("exp_")) return "experience";
  if (targetRef.startsWith("prj_")) return "projects";
  if (targetRef.startsWith("edu_")) return "education";
  if (targetRef.startsWith("skills")) return "skills";
  return "other";
}

/** Trust the server's own label, falling back to the ref it will write to. */
export function sectionOf(s: Pick<SuggestionOut, "section" | "target_ref">): SectionKey {
  const named = (s.section ?? "").trim().toLowerCase();
  if ((ORDER as readonly string[]).includes(named)) return named as SectionKey;
  return sectionOfRef(s.target_ref ?? "");
}

export type SuggestionGroup = {
  key: SectionKey;
  title: string;
  suggestions: SuggestionOut[];
  /** Every keyword this group would add, de-duplicated, in first-seen order. */
  keywords: string[];
};

/**
 * Partition suggestions into ordered, non-empty section groups.
 *
 * Order is fixed by ORDER rather than by arrival, so the list does not
 * reshuffle under the user as cards are accepted. Within a group the server's
 * order is preserved -- it is already ranked, and re-sorting here would
 * silently override that.
 */
export function groupSuggestions(suggestions: SuggestionOut[]): SuggestionGroup[] {
  const buckets = new Map<SectionKey, SuggestionOut[]>();

  for (const s of suggestions) {
    const key = sectionOf(s);
    const list = buckets.get(key) ?? [];
    list.push(s);
    buckets.set(key, list);
  }

  const keys: SectionKey[] = [...ORDER, "other"];

  return keys
    .filter((k) => (buckets.get(k)?.length ?? 0) > 0)
    .map((k) => {
      const list = buckets.get(k)!;
      const seen = new Set<string>();
      const keywords: string[] = [];
      for (const s of list) {
        for (const raw of s.keywords ?? []) {
          const kw = (raw ?? "").trim();
          if (!kw) continue;
          const norm = kw.toLowerCase();
          if (seen.has(norm)) continue;
          seen.add(norm);
          keywords.push(kw);
        }
      }
      return { key: k, title: TITLES[k], suggestions: list, keywords };
    });
}
