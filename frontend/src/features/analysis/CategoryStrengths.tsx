import { CheckCircle2 } from "lucide-react";

/**
 * "What's Working Well" — the only part of the analysis that says something
 * positive. Reference `Screenshot 2026-10-11 165736`.
 *
 * Renders nothing when the list is empty, and that is a real outcome rather
 * than a loading state: the backend returns no strengths for a category
 * scoring under half marks, drops any claim carrying a number that is not in
 * the resume, and caps the rest at three. A resume with nothing good in a
 * section gets silence, which is the honest answer and the whole reason the
 * filter exists.
 *
 * So there is deliberately no empty-state copy here. "Nothing is working
 * well in this section" is already said, more precisely and with a number
 * attached, by the score and the recommendations below it.
 */
export function CategoryStrengths({ items }: { items: string[] | undefined }) {
  if (!items || items.length === 0) return null;

  return (
    <section className="rounded-xl border border-accept-text/25 bg-accept-text/5 p-3">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="size-4 shrink-0 text-accept-text" aria-hidden />
        <h3 className="text-sm font-semibold text-content">What's working well</h3>
        <span
          className="ml-auto rounded-full bg-accept-text/15 px-2 py-0.5 text-[10px]
                     font-semibold tabular-nums text-accept-text"
        >
          {items.length} found
        </span>
      </div>

      <ul className="mt-2 space-y-1.5">
        {items.map((text, i) => (
          <li
            key={i}
            className="flex items-start gap-2 rounded-lg bg-surface-2 px-2.5 py-2"
          >
            <CheckCircle2
              className="mt-0.5 size-3.5 shrink-0 text-accept-text"
              aria-hidden
            />
            <span className="text-xs leading-relaxed text-content">{text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
