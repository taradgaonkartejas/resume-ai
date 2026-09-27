import { Check } from "lucide-react";
import type { StructuredData, TemplateOut } from "@/services";
import { ResumePreview } from "@/features/preview/ResumePreview";
import { cn } from "cn";

/**
 * The template card grid.
 *
 * Shared by the post-upload "Choose a template" step and the change-template
 * modal, so the two can never drift apart.
 *
 * Cards render the user's ACTUAL parsed content rather than sample data: the
 * renderer already exists, so a realistic preview costs nothing and makes the
 * choice meaningful.
 */
export function TemplateGrid({
  templates,
  data,
  selected,
  currentKey,
  onSelect,
  columns = 3,
}: {
  templates: TemplateOut[];
  data: StructuredData;
  selected: string;
  /** The template currently applied, badged "current". Omit in the wizard,
   *  where nothing has been applied yet. */
  currentKey?: string;
  onSelect: (key: string) => void;
  columns?: 3 | 4;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="Resume template"
      className={cn(
        "grid gap-4 p-1 sm:grid-cols-2",
        columns === 4 ? "lg:grid-cols-4" : "lg:grid-cols-3",
      )}
    >
      {templates.map((t) => {
        const active = selected === t.key;
        return (
          <button
            key={t.key}
            role="radio"
            aria-checked={active}
            onClick={() => onSelect(t.key)}
            className={cn(
              "group flex flex-col overflow-hidden rounded-xl border-2 text-left transition-colors",
              active
                ? "border-brand bg-surface-2"
                : "border-line-soft bg-surface-2 hover:border-brand-text",
            )}
          >
            {/* Name and description above the thumbnail: read what it is,
                then see it. */}
            <div className="flex items-start gap-2 p-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-content">{t.name}</p>
                <p className="mt-0.5 line-clamp-2 text-xs text-content-muted">
                  {t.description}
                </p>
              </div>
              {currentKey === t.key && (
                <span className="ml-auto shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium text-content-muted">
                  current
                </span>
              )}
            </div>

            {/* The wrapper is sized to the SCALED page (520x735 * 0.5) so the
                whole sheet is visible instead of being cropped -- you are
                choosing a layout, so the layout has to be legible. */}
            <div className="relative overflow-hidden border-t border-line-soft bg-surface-3 p-3">
              <div className="pointer-events-none mx-auto h-[368px] w-[260px]">
                <div className="origin-top-left scale-[0.5]">
                  <ResumePreview data={data} tokens={t.design_tokens} />
                </div>
              </div>
              {active && (
                <span className="absolute right-2 top-2 grid size-6 place-items-center rounded-full bg-brand text-on-brand shadow">
                  <Check className="size-3.5" aria-hidden />
                </span>
              )}
            </div>

            <p className="border-t border-line-soft px-3 py-2 text-[11px] text-content-muted">
              {t.design_tokens.font === "serif" ? "Serif" : "Sans-serif"}
              {" · "}
              {t.design_tokens.density} spacing
              {t.design_tokens.header === "split" ? " · split header" : ""}
            </p>
          </button>
        );
      })}
    </div>
  );
}
