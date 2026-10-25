import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { RecommendationCard } from "./RecommendationCard";
import { rankFindings, type Finding, type StructuredData } from "@/services";
import {
  groupFindingsByEntry,
  type EntryGroupData,
  type SectionKind,
} from "./groupFindings";
import { cn } from "cn";

export function GroupedFindings({
  findings,
  data,
  section = "experience",
  resumeId,
  onFix,
  onFixed,
}: {
  findings: Finding[];
  data: StructuredData | undefined;
  /** "experience" groups per role, "projects" per project. Default keeps the
   *  experience behaviour the component was written for. */
  section?: SectionKind;
  resumeId: string | null;
  onFix: (f: Finding) => void;
  onFixed?: (f: Finding) => void;
}) {
  const { groups, ungrouped } = groupFindingsByEntry(findings, data, section);

  return (
    <div className="space-y-2">
      {ungrouped.length > 0 && (
        <ul className="space-y-2">
          {rankFindings(ungrouped).map((f) => (
            <RecommendationCard
              key={f.id + f.target_ref}
              finding={f}
              resumeId={resumeId}
              onFix={onFix}
              onFixed={onFixed}
            />
          ))}
        </ul>
      )}

      {groups.map((group, i) => (
        <EntryGroup
          key={group.key}
          group={group}
          /* First entry open, the rest collapsed: the top entry is where most
             findings and most recruiter attention land, and five expanded
             groups is the wall of cards this grouping exists to prevent. */
          defaultOpen={i === 0}
          resumeId={resumeId}
          onFix={onFix}
          onFixed={onFixed}
        />
      ))}
    </div>
  );
}

function EntryGroup({
  group,
  defaultOpen,
  resumeId,
  onFix,
  onFixed,
}: {
  group: EntryGroupData;
  defaultOpen: boolean;
  resumeId: string | null;
  onFix: (f: Finding) => void;
  onFixed?: (f: Finding) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const points = group.findings.reduce((a, f) => a + f.points, 0);

  return (
    <section className="overflow-hidden rounded-xl border border-line-soft">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 bg-surface-2 px-3 py-2.5 text-left
                   transition hover:bg-surface-3/60
                   focus-visible:outline-2 focus-visible:outline-brand"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-content">{group.title}</p>
          {group.subtitle && (
            <p className="truncate text-[11px] text-content-muted">{group.subtitle}</p>
          )}
        </div>
        <span className="shrink-0 rounded-full bg-brand/15 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-brand-text">
          {group.findings.length}
        </span>
        {points > 0 && (
          <span className="shrink-0 text-[10px] tabular-nums text-content-muted">
            +{points} pts
          </span>
        )}
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-content-muted transition-transform",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>

      {open && (
        <ul className="space-y-2 border-t border-line-soft bg-surface-1/40 p-2">
          {group.findings.map((f) => (
            <RecommendationCard
              key={f.id + f.target_ref}
              finding={f}
              resumeId={resumeId}
              onFix={onFix}
              onFixed={onFixed}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
