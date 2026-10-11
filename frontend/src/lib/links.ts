/**
 * Profile-link labels.
 *
 * A resume that writes "LinkedIn" over a hyperlink is doing the right thing,
 * and every surface should reproduce that rather than printing a
 * 54-character URL into the contact header.
 *
 * This table is mirrored in `backend/app/services/resume_ops.py`
 * (`PROFILE_HOSTS`). The two are pinned together by
 * `backend/tests/test_profile_links.py`, which reads this file — so a host
 * added on one side and not the other fails the suite rather than quietly
 * rendering a bare domain in one export and a label in another.
 */
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

/** Bare lowercase host: no scheme, no `www.`, no port, no credentials. */
export function hostOf(url: string): string {
  const rest = url.split("://").pop() ?? url;
  const host = (rest.split("/")[0] ?? "")
    .split("?")[0]
    .split("#")[0]
    .split("@")
    .pop()!
    .split(":")[0]
    .toLowerCase();
  return host.startsWith("www.") ? host.slice(4) : host;
}

/** The allowlist key this URL belongs to, or null.
 *
 *  Subdomains count — `gist.github.com` is still GitHub — but a plain suffix
 *  match would also accept `github.com.phishing.ru`, so it is anchored on a
 *  dot boundary. */
export function knownHost(url: string): string | null {
  const host = hostOf(url);
  for (const key of Object.keys(PROFILE_HOSTS)) {
    if (host === key || host.endsWith("." + key)) return key;
  }
  return null;
}

/** "https://www.linkedin.com/in/tejas-taradgaonkar" -> "LinkedIn" */
export function profileLabel(url: string): string {
  const key = knownHost(url);
  return key ? PROFILE_HOSTS[key] : hostOf(url) || url;
}

/** Ensure a bare "linkedin.com/in/x" is still a navigable href. */
export function hrefFor(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}

/**
 * Pair each URL with its label, disambiguating collisions.
 *
 * Two GitHub links both labelled "GitHub" would be indistinguishable, so a
 * repeated label takes the last path segment: "GitHub (taradgaonkartejas)".
 */
export function labelLinks(urls: string[]): { url: string; label: string }[] {
  const labels = urls.map(profileLabel);
  return urls.map((url, i) => {
    let label = labels[i];
    if (labels.filter((l) => l === label).length > 1) {
      const tail = (url.split("://").pop() ?? "").split("/").slice(1).filter(Boolean);
      if (tail.length) label = `${label} (${tail[tail.length - 1]})`;
    }
    return { url, label };
  });
}
