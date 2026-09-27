/**
 * Resume strength bands.
 *
 * Lives outside the component file so ScoreRing.tsx exports components only
 * (react-refresh/only-export-components).
 *
 * Deliberately honest: the reference screenshot labels 83% "Excellent!",
 * which is flattery rather than feedback.
 */
export function band(score: number): { label: string; className: string } {
  if (score >= 80) return { label: "Strong", className: "text-accept-text" };
  if (score >= 60) return { label: "Good", className: "text-brand-text" };
  return { label: "Needs work", className: "text-warning" };
}
