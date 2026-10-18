import { History, Target } from "lucide-react";
import { cn } from "cn";

/**
 * The ScoringAgent's prose for one step.
 *
 * This layer has existed since early on: an LLM call on every analysis,
 * persisted into the report, returned by the API -- and rendered by nothing.
 * We paid for it every time and showed the user none of it.
 *
 * It ships with a staleness stamp rather than a freshness claim. The prose is
 * a SNAPSHOT of the resume at analysis time; the findings beside it are
 * recomputed on every request. The moment a bullet is edited the two can
 * disagree, and prose is the half that reads as authoritative -- "the summary
 * leans on generic filler" sitting above zero findings, because the filler was
 * just removed, looks like the product is broken.
 *
 * Deliberately NOT re-generated on step entry: that would be an LLM call every
 * time a step is opened, five per session, each 6-34s against a measured token
 * budget. Telling the user how old it is costs nothing and is honest.
 */
export function CommentaryBlock({
  text,
  stale,
  label = "Overall Assessment",
}: {
  text: string;
  stale: boolean;
  label?: string;
}) {
  if (!text.trim()) return null;

  return (
    <section
      className={cn(
        "rounded-xl border border-line-soft bg-surface-2 p-4",
        // Dimmed, not hidden. The insight is still worth having; it just
        // needs to stop claiming to describe the current text.
        stale && "opacity-70",
      )}
      aria-label={label}
    >
      <div className="flex items-center gap-2">
        <Target className="size-4 text-brand-text" aria-hidden />
        <h3 className="text-sm font-semibold text-content">{label}</h3>
        {stale && (
          <span
            className="ml-auto inline-flex items-center gap-1 rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium text-content-muted"
            title="Your resume has changed since this was written. Re-run the analysis to refresh it."
          >
            <History className="size-3" aria-hidden />
            From your last analysis
          </span>
        )}
      </div>
      <p className="mt-2 text-sm leading-relaxed text-content-muted">{text}</p>
    </section>
  );
}
