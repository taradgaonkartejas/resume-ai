import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { keys, useUpdateResumeData } from "@/api/queries";
import type { ResumeOut, StructuredData } from "@/services";

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * Debounced autosave for the guided editor. No Save button — the reference
 * has none, and a step-by-step flow where you can lose an edit by pressing an
 * arrow is worse than useless.
 *
 * SINGLE SOURCE OF TRUTH. There is exactly one copy of the resume in the
 * client: the React Query cache entry `keys.resume(id)`. The editor does not
 * hold a draft; it writes straight through to that entry and renders from it,
 * which is the same object the live preview reads.
 *
 * This hook used to keep a local `draft` in useState and adopt the server
 * copy ONLY when the resume id changed:
 *
 *     if (syncedId.current === resumeId) return;   // never re-syncs
 *
 * That produced the bug this rewrite fixes. Accepting an AI suggestion
 * invalidates keys.resume(id); the refetch updated the preview, while the
 * editor's frozen draft kept showing the pre-AI text. Worse than the stale
 * textarea: `update()` PUTs the WHOLE document, so the next keystroke in any
 * field wrote the pre-AI draft back and silently reverted the accepted
 * suggestion. Two copies of mutable state, one of them write-back — the
 * display bug and the data loss were the same defect.
 *
 * The id-only guard was not arbitrary, though, and the reason it existed is
 * still live: every save invalidates, so re-adopting server data naively
 * would yank the cursor mid-keystroke. Writing through to the cache solves
 * that differently — the cache already holds what the user typed, so a
 * re-render from it is a no-op for the input. The two remaining ways a stale
 * document could land on top of fresh typing are both handled below:
 *
 *   1. a GET already in flight when the user types (cancelQueries), and
 *   2. our own PUT echoing the older document back while the user kept
 *      typing during the save (re-apply `pending` in onSuccess).
 */
export function useAutosave(
  resumeId: string | null,
  serverData: StructuredData | undefined,
  delayMs = 800,
) {
  const qc = useQueryClient();
  const save = useUpdateResumeData(resumeId);
  const [state, setState] = useState<SaveState>("idle");

  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<StructuredData | null>(null);
  // Mutation identity changes between renders; a ref keeps the flush callback
  // stable so the unmount effect does not re-run on every keystroke. Written
  // in an effect, not during render — under concurrent rendering a render can
  // be discarded, and a ref mutated there would leak from work that never
  // committed.
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  });

  /** Write through to the one copy. The preview reads this same entry, so
   *  manual edits now show there as the user types rather than a debounce
   *  later — the AI path already behaved that way. */
  const writeCache = useCallback(
    (next: StructuredData) => {
      if (!resumeId) return;
      qc.setQueryData<ResumeOut>(keys.resume(resumeId), (prev) =>
        prev ? { ...prev, structured_data: next } : prev,
      );
    },
    [qc, resumeId],
  );

  const flush = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const next = pending.current;
    if (!next) return;
    pending.current = null;
    setState("saving");
    saveRef.current.mutate(next, {
      onSuccess: () => {
        // useUpdateResumeData.onSuccess has just replaced the cache entry
        // with the server's echo of `next`. If the user typed during the
        // round trip, that echo is already out of date and those keystrokes
        // would vanish on the next render. Put them back.
        if (pending.current) writeCache(pending.current);
        setState((s) => (s === "saving" ? "saved" : s));
      },
      onError: () => setState("error"),
    });
  }, [writeCache]);

  const update = useCallback(
    (next: StructuredData) => {
      // A GET issued before this keystroke would resolve after it and
      // overwrite the cache with the pre-edit document. Cancelling is the
      // standard optimistic-update guard and is a no-op when nothing is in
      // flight.
      if (resumeId) void qc.cancelQueries({ queryKey: keys.resume(resumeId) });
      writeCache(next);
      setState("dirty");
      pending.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, delayMs);
    },
    [delayMs, flush, qc, resumeId, writeCache],
  );

  // Switching resumes starts a fresh status line. No draft to re-seed: the
  // editor renders from whatever keys.resume(newId) holds.
  //
  // Adjusted during render rather than in an effect. React 19's
  // `react-hooks/set-state-in-effect` rule rejects the effect form, and this
  // is the pattern it points at: compare against the previous value, and
  // React re-runs the render immediately without committing the stale status.
  const [lastResumeId, setLastResumeId] = useState(resumeId);
  if (lastResumeId !== resumeId) {
    setLastResumeId(resumeId);
    setState("idle");
  }

  // Flush on unmount / resume switch so the last edit is never lost.
  useEffect(() => {
    return () => {
      if (pending.current) {
        const next = pending.current;
        pending.current = null;
        if (timer.current) clearTimeout(timer.current);
        saveRef.current.mutate(next);
      }
    };
  }, [resumeId]);

  // Let "Saved" fade rather than linger as a stale claim.
  useEffect(() => {
    if (state !== "saved") return;
    const t = setTimeout(() => setState((s) => (s === "saved" ? "idle" : s)), 2000);
    return () => clearTimeout(t);
  }, [state]);

  // Named `draft` so call sites read unchanged, but it is no longer a draft:
  // it is the cache entry, the same object the preview renders.
  return { draft: serverData, update, flush, state };
}
