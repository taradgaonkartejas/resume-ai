import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";
import type { StructuredData, TemplateOut } from "@/services";
import { ResumePreview } from "@/features/preview/ResumePreview";
import { cn } from "cn";

/** The page ResumePreview draws, in CSS px. Scaling is derived from these,
 *  so the thumbnail cannot drift from the real sheet. */
const PAGE_W = 520;
const PAGE_H = 735;

/**
 * Scale the preview sheet to the width its card actually has.
 *
 * A fixed `scale-[0.5]` into a fixed `w-[260px]` box only lined up at one
 * card width; at every other breakpoint the sheet floated in a gutter. The
 * card width is not knowable in CSS here (no element query for a transform
 * scale factor), so it is measured.
 */
function useFitToWidth() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Observe rather than read once: the grid is responsive and the modal
    // can be resized while open.
    const ro = new ResizeObserver(([entry]) => {
      setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const scale = width > 0 ? width / PAGE_W : 0;
  return {
    ref,
    // Height follows the scaled page so the card never crops it or leaves
    // dead space under it.
    boxStyle: {
      width: "100%",
      height: scale > 0 ? PAGE_H * scale : undefined,
      overflow: "hidden",
    } as const,
    pageStyle: {
      width: PAGE_W,
      transform: `scale(${scale})`,
      // Hide the unscaled flash on the first paint, before measurement.
      visibility: (scale > 0 ? "visible" : "hidden") as "visible" | "hidden",
    } as const,
  };
}

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
      {templates.map((t) => (
        <TemplateCard
          key={t.key}
          template={t}
          data={data}
          active={selected === t.key}
          isCurrent={currentKey === t.key}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

/**
 * One card.
 *
 * Split out of the grid because each card measures its own width, and a hook
 * cannot be called inside a .map() callback.
 */
function TemplateCard({
  template: t,
  data,
  active,
  isCurrent,
  onSelect,
}: {
  template: TemplateOut;
  data: StructuredData;
  active: boolean;
  isCurrent: boolean;
  onSelect: (key: string) => void;
}) {
  const { ref, boxStyle, pageStyle } = useFitToWidth();

  return (
    <button
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
          <p className="truncate text-sm font-semibold text-content">
            {t.name}
          </p>
          <p className="mt-0.5 line-clamp-2 text-xs text-content-muted">
            {t.description}
          </p>
        </div>
        {isCurrent && (
          <span className="ml-auto shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[10px] font-medium text-content-muted">
            current
          </span>
        )}
      </div>

      {/* The sheet is scaled to whatever width the card actually has,
                rather than a hard-coded 0.5 into a fixed 260px box which left
                a gutter either side and changed with the breakpoint. */}
      <div className="relative overflow-hidden border-t border-line-soft bg-surface-3 p-3">
        <div className="pointer-events-none" ref={ref} style={boxStyle}>
          <div className="origin-top-left" style={pageStyle}>
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
}
