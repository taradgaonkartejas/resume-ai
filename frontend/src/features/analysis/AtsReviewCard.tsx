import { ArrowRight, ScanSearch } from "lucide-react";
import type { AtsReview } from "@/services";

/**
 * Advisory machine-readability notes from AtsAgent.
 *
 * Renders nothing at all when there is nothing to say, which is the common
 * case and not an error state: the agent is gated (a tidy resume never calls
 * a model) and stays silent without a provider key. An empty-state card here
 * would imply a feature had failed when it had simply found no problem.
 *
 * Deliberately separated from the score breakdown above it. These points are
 * NOT in the 100 — the number is deterministic and an LLM must never move
 * it — so presenting them inside a scored category would be a lie about
 * where the score comes from.
 */
export function AtsReviewCard({ review }: { review: AtsReview }) {
  const relabels = review?.relabels ?? [];
  const expansions = review?.expansions ?? [];
  const note = review?.note?.trim() ?? "";

  if (relabels.length === 0 && expansions.length === 0 && !note) return null;

  return (
    <section className="rounded-xl border border-line-soft bg-surface-2 p-4">
      <header className="flex items-start gap-2">
        <ScanSearch className="mt-0.5 size-4 shrink-0 text-brand-text" aria-hidden />
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">Machine readability</h3>
          <p className="mt-0.5 text-xs text-content-muted">
            How an applicant tracking system will read this. Advisory — these
            don&apos;t change your score.
          </p>
        </div>
      </header>

      {note && <p className="mt-3 text-xs text-content">{note}</p>}

      {relabels.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-[11px] font-medium uppercase tracking-wide text-content-muted">
            Section headings
          </p>
          {relabels.map((item) => (
            <div
              key={item.ref}
              className="rounded-lg border border-line-soft bg-surface-1 px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-1.5 text-xs">
                <span className="rounded bg-surface-3 px-1.5 py-0.5 font-medium">
                  {item.current_title}
                </span>
                {item.suggested_title && (
                  <>
                    <ArrowRight className="size-3 text-content-muted" aria-hidden />
                    <span className="rounded bg-mark px-1.5 py-0.5 font-medium text-on-mark">
                      {item.suggested_title}
                    </span>
                  </>
                )}
              </div>
              {item.reason && (
                <p className="mt-1.5 text-xs text-content-muted">{item.reason}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {expansions.length > 0 && (
        <div className="mt-3 space-y-2">
          <p className="text-[11px] font-medium uppercase tracking-wide text-content-muted">
            Spell out acronyms
          </p>
          <p className="text-xs text-content-muted">
            Workday indexes an acronym and its expansion as different terms, so
            carrying both matches more searches.
          </p>
          <ul className="space-y-1">
            {expansions.map((item) => (
              <li
                key={item.acronym}
                className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line-soft bg-surface-1 px-3 py-1.5 text-xs"
              >
                <span className="rounded bg-surface-3 px-1.5 py-0.5 font-medium">
                  {item.acronym}
                </span>
                <ArrowRight className="size-3 text-content-muted" aria-hidden />
                <span className="min-w-0 text-content">{item.expanded}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
