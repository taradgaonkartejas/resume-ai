import { useCallback, useEffect, useRef, useState } from "react";
import { useUpdateResumeData } from "@/api/queries";
import type { StructuredData } from "@/services";

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

/**
 * Debounced autosave for the guided editor. No Save button — the reference
 * has none, and a step-by-step flow where you can lose an edit by pressing an
 * arrow is worse than useless.
 *
 * Design notes:
 *
 * - The draft is held locally so typing never waits on the network. The server
 *   copy is only pulled in when the resume id changes, otherwise every
 *   invalidation (the steps refetch fires on each save) would yank the
 *   cursor back mid-keystroke.
 * - A pending timer is flushed on unmount and on id change, so stepping away
 *   or closing the pane cannot silently drop the last edit.
 * - `saved` decays back to `idle` so the status line does not permanently
 *   claim success from a minute ago.
 */
export function useAutosave(
  resumeId: string | null,
  serverData: StructuredData | undefined,
  delayMs = 800,
) {
  const save = useUpdateResumeData(resumeId);
  const [draft, setDraft] = useState<StructuredData | undefined>(serverData);
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

  // Adopt the server copy only when we switch resumes or receive the first
  // payload. Re-syncing on every server response would clobber in-flight typing.
  const syncedId = useRef<string | null>(null);
  useEffect(() => {
    if (!serverData) return;
    if (syncedId.current === resumeId) return;
    syncedId.current = resumeId;
    setDraft(serverData);
    setState("idle");
  }, [resumeId, serverData]);

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
      onSuccess: () => setState((s) => (s === "saving" ? "saved" : s)),
      onError: () => setState("error"),
    });
  }, []);

  const update = useCallback(
    (next: StructuredData) => {
      setDraft(next);
      setState("dirty");
      pending.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(flush, delayMs);
    },
    [delayMs, flush],
  );

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

  return { draft, update, flush, state };
}
