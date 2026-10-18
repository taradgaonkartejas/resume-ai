import { useState } from "react";

/**
 * Stable drag ids for a list whose items have none of their own.
 *
 * `structured_data.experience` entries carry no id, and the editor clones the
 * whole document on every keystroke, so neither the array index nor object
 * identity survives as a key. dnd-kit needs ids that do NOT change as items
 * move: with index-as-id, the id of the card under the cursor changes while
 * you are dragging it and the list fights you.
 *
 * Implementation notes, both of which are lint-enforced and were got wrong
 * first time round:
 *
 *  - **No ref mutation during render.** The obvious version keeps the id
 *    array in a `useRef` and grows it inline. That reads and writes a ref
 *    while rendering, which is unsafe once React is free to render
 *    concurrently or twice in StrictMode.
 *  - **No `Math.random()` during render.** Ids come from a monotonic counter
 *    carried in state, so a render is a pure function of its inputs.
 *
 * Adjusting state *during* render (rather than in an effect) is React's
 * documented pattern for deriving state from changed props, and it matters
 * here: React discards the in-progress render and re-renders before
 * committing, so the list never commits a frame in which an entry has no id.
 */
export function useStableIds(count: number) {
  const [state, setState] = useState(() => ({
    ids: Array.from({ length: count }, (_, i) => `role-${i}`),
    next: count,
  }));

  if (state.ids.length !== count) {
    setState((prev) => {
      if (prev.ids.length === count) return prev;
      if (prev.ids.length > count) {
        return { ...prev, ids: prev.ids.slice(0, count) };
      }
      const ids = [...prev.ids];
      let next = prev.next;
      while (ids.length < count) ids.push(`role-${next++}`);
      return { ids, next };
    });
  }

  return {
    ids: state.ids,
    /**
     * Apply to the id list the same permutation the caller applies to the
     * data. If these two ever diverge, every later drag addresses the wrong
     * card -- so they are always moved together, in one handler.
     */
    reorder: (from: number, to: number) =>
      setState((prev) => {
        const ids = [...prev.ids];
        const [moved] = ids.splice(from, 1);
        ids.splice(to, 0, moved);
        return { ...prev, ids };
      }),
  };
}
