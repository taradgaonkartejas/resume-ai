import { useEffect, useId, useRef } from "react";
import { ChevronDown, Lightbulb, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "cn";

/**
 * Field primitives for the guided editor, on shadcn/base-ui components.
 *
 * These know nothing about findings or scores — they render a labelled
 * control and report changes upward. Keeping them dumb is what lets the step
 * components stay readable.
 */

export function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  highlight = false,
  id,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  /** True when a finding points here: draws attention without stealing focus. */
  highlight?: boolean;
  id?: string;
}) {
  const auto = useId();
  const fieldId = id ?? auto;
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={fieldId} className="text-xs text-content-muted">
        {label}
      </Label>
      <Input
        id={fieldId}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn(highlight && "border-warning/70")}
      />
    </div>
  );
}

export function TextAreaField({
  label,
  value,
  onChange,
  placeholder,
  rows = 4,
  highlight = false,
  id,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  rows?: number;
  highlight?: boolean;
  id?: string;
  hint?: string;
}) {
  const auto = useId();
  const fieldId = id ?? auto;
  const words = value.trim() ? value.trim().split(/\s+/).length : 0;
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor={fieldId} className="text-xs text-content-muted">
          {label}
        </Label>
        <span className="text-[10px] tabular-nums text-content-muted">
          {words} {words === 1 ? "word" : "words"}
          {hint && ` · ${hint}`}
        </span>
      </div>
      <Textarea
        id={fieldId}
        rows={rows}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className={cn("leading-relaxed", highlight && "border-warning/70")}
      />
    </div>
  );
}

/**
 * Start / end dates with a "Currently work here" toggle.
 *
 * Two rules the reference implies and the data model enforces:
 *   - Ticking "current" disables the end date rather than hiding it, so the
 *     control does not jump around and the previous value is still visible.
 *   - The end date is NOT cleared on toggle. Un-ticking restores what was
 *     there, which matters because mis-clicking the switch is easy and
 *     silently destroying a date the user typed is not recoverable.
 * `date_label()` ignores end_date whenever current is true, so a retained
 * value can never leak into the rendered resume.
 */
export function DateRangeField({
  startDate,
  endDate,
  current,
  rawDates,
  onChange,
  highlight = false,
  id,
  currentLabel = "I currently work here",
}: {
  startDate: string;
  endDate: string;
  current: boolean;
  /** Set when a legacy string could not be split; shown instead of inputs. */
  rawDates?: string;
  onChange: (patch: {
    start_date?: string;
    end_date?: string;
    current?: boolean;
    raw_dates?: string;
  }) => void;
  highlight?: boolean;
  id?: string;
  currentLabel?: string;
}) {
  const auto = useId();
  const base = id ?? auto;

  // A range we refused to parse. Offer conversion rather than silently
  // reinterpreting what the user originally wrote.
  if (rawDates) {
    return (
      <div
        className={cn(
          "rounded-lg border border-dashed border-warning/60 bg-warning/5 p-2.5",
          highlight && "border-warning",
        )}
        id={base}
      >
        <p className="text-xs font-medium">Dates: {rawDates}</p>
        <p className="mt-1 text-[11px] leading-relaxed text-content-muted">
          We could not read this as a start and end date, so we left it exactly
          as written rather than guessing.
        </p>
        <button
          type="button"
          onClick={() =>
            onChange({ raw_dates: "", start_date: rawDates, end_date: "",
                       current: false })
          }
          className="mt-1.5 text-[11px] font-medium text-brand-text hover:underline"
        >
          Enter dates manually
        </button>
      </div>
    );
  }

  return (
    <div className="grid gap-2" id={base}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label htmlFor={`${base}-start`} className="text-xs text-content-muted">
            Start date
          </Label>
          <Input
            id={`${base}-start`}
            value={startDate}
            placeholder="Jan 2021"
            onChange={(e) => onChange({ start_date: e.target.value })}
            className={cn(highlight && "border-warning/70")}
          />
        </div>
        <div className="grid gap-1.5">
          <Label
            htmlFor={`${base}-end`}
            className={cn(
              "text-xs text-content-muted",
              current && "opacity-50",
            )}
          >
            End date
          </Label>
          <Input
            id={`${base}-end`}
            value={current ? "" : endDate}
            disabled={current}
            placeholder={current ? "Present" : "Dec 2023"}
            onChange={(e) => onChange({ end_date: e.target.value })}
            className={cn(highlight && !current && "border-warning/70")}
          />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Switch
          id={`${base}-current`}
          checked={current}
          onCheckedChange={(checked: boolean) => onChange({ current: checked })}
        />
        <Label
          htmlFor={`${base}-current`}
          className="cursor-pointer text-xs font-normal text-content-muted"
        >
          {currentLabel}
        </Label>
      </div>
    </div>
  );
}

/**
 * The collapsible "Tips and Recommendations" panel from the reference.
 * Collapsed by default: advice the user did not ask for should not push the
 * fields they came to edit below the fold.
 */
export function TipsPanel({ tips }: { tips: string[] }) {
  if (tips.length === 0) return null;
  return (
    <Collapsible className="rounded-lg border border-line-soft bg-surface-2">
      <CollapsibleTrigger
        className="group flex w-full items-center gap-2 px-3 py-2 text-left
                   text-xs font-medium text-content-muted hover:text-content"
      >
        <Lightbulb className="size-3.5 shrink-0" aria-hidden />
        Tips and recommendations
        <ChevronDown
          className="ml-auto size-3.5 shrink-0 transition-transform
                     group-data-[panel-open]:rotate-180"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="space-y-1.5 border-t border-line-soft px-3 py-2.5">
          {tips.map((tip, i) => (
            <li key={i} className="flex gap-2 text-[11px] leading-relaxed text-content-muted">
              <span className="mt-1.5 size-1 shrink-0 rounded-full bg-brand" />
              {tip}
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** A bullet row that grows with its content. */
export function BulletRow({
  value,
  onChange,
  onRemove,
  focus = false,
  highlight = false,
  id,
}: {
  value: string;
  onChange: (v: string) => void;
  onRemove: () => void;
  focus?: boolean;
  highlight?: boolean;
  id?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow to fit, so long bullets are readable instead of a 2-line peephole.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  useEffect(() => {
    if (focus && ref.current) {
      ref.current.focus();
      ref.current.setSelectionRange(value.length, value.length);
    }
    // Only when the focus request arrives, not on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  const words = value.trim() ? value.trim().split(/\s+/).length : 0;

  return (
    <div className="group flex items-start gap-2">
      <span className="mt-3 size-1.5 shrink-0 rounded-full bg-content-muted" aria-hidden />
      <div className="min-w-0 flex-1">
        <Textarea
          id={id}
          ref={ref}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={cn(
            "resize-none overflow-hidden py-2 leading-relaxed",
            highlight && "border-warning/70",
          )}
        />
        {words > 45 && (
          <p className="mt-1 text-[10px] text-warning">
            {words} words — long bullets get skimmed past.
          </p>
        )}
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label="Delete bullet"
        className="mt-1.5 rounded-md p-1.5 text-content-muted opacity-0 transition
                   hover:bg-surface-3 hover:text-reject-text focus-visible:opacity-100
                   group-hover:opacity-100"
      >
        <Trash2 className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
