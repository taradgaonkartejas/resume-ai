import { useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";

/**
 * Navigation helpers, kept OUT of routes.tsx.
 *
 * Exporting hooks alongside the <AppRoutes> component trips
 * react-refresh/only-export-components: a file that exports both a component
 * and non-components breaks Fast Refresh. Same reason lib/score.ts exists.
 *
 * Centralising the path strings means route shapes are defined once --
 * a typo'd literal would silently fall through to the catch-all redirect.
 */

export type LeftTabParam = "analysis" | "tailor";

/** The resume id from the path, or null on routes that have none. */
export function useResumeIdParam(): string | null {
  const { resumeId } = useParams<{ resumeId: string }>();
  return resumeId ?? null;
}

export function useNav() {
  const navigate = useNavigate();

  return {
    library: useCallback(() => navigate("/resumes"), [navigate]),

    templateStep: useCallback(
      (resumeId: string) => navigate(`/resumes/${resumeId}/template`),
      [navigate],
    ),

    /**
     * `replace` matters after the template step: the wizard is one-way, and
     * leaving it in history means Back returns the user to a choice they
     * already made.
     */
    workspace: useCallback(
      (resumeId: string, tab?: LeftTabParam, opts?: { replace?: boolean }) =>
        navigate(
          tab ? `/resumes/${resumeId}?tab=${tab}` : `/resumes/${resumeId}`,
          { replace: opts?.replace ?? false },
        ),
      [navigate],
    ),
  };
}
