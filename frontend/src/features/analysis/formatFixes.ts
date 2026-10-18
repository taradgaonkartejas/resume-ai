import type { Finding, SuggestionOut } from "@/services";

export type BulkReport = {
  /** Fixes written straight into the resume (deterministic). */
  applied: number;
  /** AI drafts created, awaiting the user's review. */
  drafted: number;
  /** Human-readable reasons, one per item that could not be handled. */
  failed: string[];
  /** Drafts keyed `${finding.id}::${target_ref}` for the cards to render. */
  drafts: Record<string, SuggestionOut>;
};

/**
 * Run the Step-4 bulk action over a set of findings.
 *
 * Pure orchestration, deliberately outside the component: the ordering and
 * the failure handling are the parts that can be wrong in ways a glance at
 * the UI would not reveal, so they are testable in isolation
 * (`scripts/test-format-fixes.mjs`).
 *
 * Two rules it exists to enforce:
 *
 * 1. **Deterministic fixes run first.** A reorder is refused by the server
 *    while any suggestion is pending on that section, so drafting before
 *    reordering would make the reorder 409 against drafts this very function
 *    had just created.
 * 2. **One refusal must not abandon the queue.** The writer declining
 *    because the missing number is one only the user knows is
 *    corpus-approved behaviour, not an outage, and it must not stop the
 *    remaining items from being attempted.
 */
export async function runFormatFixes({
  findings,
  rerank,
  rewrite,
  onProgress,
}: {
  findings: Finding[];
  rerank: (targetRef: string) => Promise<unknown>;
  rewrite: (args: { targetRef: string; findingId: string }) => Promise<SuggestionOut>;
  onProgress?: (done: number, total: number) => void;
}): Promise<BulkReport> {
  const auto = findings.filter((f) => f.action === "rerank");
  const draftable = findings.filter((f) => f.action === "rewrite");
  const queue = [...auto, ...draftable];

  const report: BulkReport = { applied: 0, drafted: 0, failed: [], drafts: {} };
  onProgress?.(0, queue.length);

  for (const [i, f] of queue.entries()) {
    try {
      if (f.action === "rerank") {
        await rerank(f.target_ref);
        report.applied += 1;
      } else {
        const s = await rewrite({ targetRef: f.target_ref, findingId: f.id });
        report.drafts[`${f.id}::${f.target_ref}`] = s;
        report.drafted += 1;
      }
    } catch (e) {
      report.failed.push((e as Error)?.message || `Could not fix: ${f.message}`);
    }
    onProgress?.(i + 1, queue.length);
  }

  return report;
}

/** Split a step's findings by how they can be resolved. */
export function partitionFormatFindings(findings: Finding[]) {
  const auto = findings.filter((f) => f.action === "rerank");
  const draftable = findings.filter((f) => f.action === "rewrite");
  // Anything with no action needs information or a judgement only the user
  // has -- a missing skills section, a resume that is simply too short.
  const manual = findings.filter((f) => !f.action);
  return { auto, draftable, manual, actionable: auto.length + draftable.length };
}
