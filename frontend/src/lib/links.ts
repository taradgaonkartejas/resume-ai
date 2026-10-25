/**
 * Profile-link helpers shared by the preview and the editor.
 *
 * Display decision (2026-10-24, user-directed): links render as brand-label
 * hyperlinks ("LinkedIn", "GitHub") on PDF / DOCX / preview — what every
 * reference resume does — while TXT keeps the short address, because it has
 * no annotation layer and would otherwise lose the URL with the label.
 * Round-trip stays safe: the parser reads link annotations, not text.
 *
 * This reverses the earlier "address, not label" ruling; the evidence both
 * times is in the git log, and the twin table lives in resume_ops.py
 * (PROFILE_HOSTS). The two tables must stay identical.
 */

/** Host → display label. Mirrors PROFILE_HOSTS in resume_ops.py. */
export const PROFILE_HOSTS: Record<string, string> = {
  "linkedin.com": "LinkedIn",
  "github.com": "GitHub",
  "gitlab.com": "GitLab",
  "bitbucket.org": "Bitbucket",
  "stackoverflow.com": "Stack Overflow",
  "medium.com": "Medium",
  "dev.to": "DEV",
  "kaggle.com": "Kaggle",
  "leetcode.com": "LeetCode",
  "hackerrank.com": "HackerRank",
  "behance.net": "Behance",
  "dribbble.com": "Dribbble",
  "x.com": "X",
  "twitter.com": "X",
  "youtube.com": "YouTube",
  "orcid.org": "ORCID",
  "scholar.google.com": "Google Scholar",
};

/** Make a bare host usable as an href. */
export function hrefFor(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}

/**
 * https://www.linkedin.com/in/username/ -> linkedin.com/in/username
 *
 * Mirrors short_url() in resume_ops.py; the preview has to show exactly
 * what the exporter will print.
 */
export function shortUrl(url: string): string {
  return (url ?? "")
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/^www\./i, "")
    .replace(/\/+$/, "");
}

/**
 * The word printed for a link. Mirrors link_label() in resume_ops.py:
 * recognised profile hosts get their brand name; anything else falls back
 * to the short address (an unrecognised URL is the only information a
 * reader has, and printing "Website" would hide it).
 */
export function linkLabel(url: string): string {
  const host = shortUrl(url).split("/", 1)[0].toLowerCase();
  return PROFILE_HOSTS[host] ?? shortUrl(url);
}
