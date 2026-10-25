/**
 * Name-choice helpers for the preview/PDF icon set. Pure string functions,
 * kept out of `icons.tsx` because eslint's react-refresh rule wants
 * components-only files.
 */

/** Mirrors `_HEADING_ICONS` in export_service.py; same fallback. */
export function headingIcon(title: string): string {
  const t = title.toLowerCase();
  if (t.includes("summary")) return "user";
  if (t.includes("experience") || t.includes("work")) return "briefcase";
  if (t.includes("project")) return "folder";
  if (t.includes("education")) return "grad-cap";
  if (t.includes("skill")) return "code";
  if (t.includes("certif")) return "award";
  return "chevron";
}

/** Mirrors `_contact_icon`: the map is by which field the line carries. */
export function contactIcon(line: string): string {
  if (line.includes("@")) return "mail";
  if (/^[+\\d][\\d\\s()-]+$/.test(line.trim())) return "phone";
  return "map-pin";
}

/** Mirrors `_link_icon`. */
export function linkIcon(url: string): string {
  const u = url.toLowerCase();
  if (u.includes("linkedin.com")) return "linkedin";
  if (u.includes("github.com") || u.includes("gitlab.com")) return "git-branch";
  return "globe";
}

