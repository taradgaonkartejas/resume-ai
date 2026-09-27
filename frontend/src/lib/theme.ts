/**
 * Theme persistence — per user, not per browser.
 *
 * Five demo users share one machine, so a single global theme key would mean
 * switching user silently rewrites someone else's preference. Keying by user
 * id keeps the switcher honest: each persona keeps the theme it was left in.
 *
 * Pure module, no React. The same key format is duplicated by the anti-flash
 * script in index.html — see the warning on THEME_KEY_PREFIX before editing.
 */

export type Theme = "dark" | "light";

/**
 * WARNING: this prefix is hard-coded a second time in index.html's inline
 * boot script, which has to run before any module loads to avoid a white
 * flash. Changing it here without changing it there degrades silently to
 * "always dark on first paint", which is easy to miss. Keep them in step.
 */
const THEME_KEY_PREFIX = "resumeai.theme.";

/** Matches index.html and the `dark` product default. */
export const DEFAULT_THEME: Theme = "dark";

/**
 * Users who have never touched the toggle share one key, so the choice
 * survives until they sign in as somebody. Without this, the theme picked
 * on the loading screen would be thrown away the moment a user resolved.
 */
const ANON_KEY = `${THEME_KEY_PREFIX}anonymous`;

function keyFor(userId: string | null): string {
  return userId ? `${THEME_KEY_PREFIX}${userId}` : ANON_KEY;
}

function isTheme(value: unknown): value is Theme {
  return value === "dark" || value === "light";
}

/**
 * Read a user's stored theme, falling back to the product default.
 *
 * localStorage throws in private-mode Safari and when storage is disabled by
 * policy. A theme preference is not worth crashing the app over, so every
 * access is guarded and a failure just means "use the default".
 */
export function readTheme(userId: string | null): Theme {
  try {
    const stored = localStorage.getItem(keyFor(userId));
    if (isTheme(stored)) return stored;

    // No preference for this user yet. Inherit whatever was chosen before
    // they were identified, so picking light on the library screen and then
    // switching user does not snap back to dark.
    const anon = localStorage.getItem(ANON_KEY);
    if (isTheme(anon)) return anon;
  } catch {
    /* storage unavailable — fall through to the default */
  }
  return DEFAULT_THEME;
}

export function writeTheme(userId: string | null, theme: Theme): void {
  try {
    localStorage.setItem(keyFor(userId), theme);
    // Mirror to the anonymous slot so the preference carries into the next
    // user who has never set one.
    localStorage.setItem(ANON_KEY, theme);
  } catch {
    /* storage unavailable — the theme still applies for this session */
  }
}

/**
 * Apply the theme to the document.
 *
 * The `dark` class is the single switch the whole palette hangs off
 * (`@custom-variant dark` in index.css), and `color-scheme` is what makes
 * native scrollbars, date pickers and form controls follow suit — CSS sets
 * it per-theme too, this keeps the two from drifting mid-session.
 */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
}

export function otherTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark";
}
