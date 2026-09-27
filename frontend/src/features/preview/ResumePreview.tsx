import { DEFAULT_DESIGN_TOKENS, type DesignTokens, type StructuredData } from "@/services";
import { dateLabel } from "@/lib/dates";
import { cn } from "cn";

/**
 * Renders structured_data as an A4-proportioned page.
 *
 * Styling comes from the template's design_tokens, which the BACKEND defines
 * in seed.py and the PDF exporter reads too. One definition, two renderers —
 * so adding a template is a backend-only change and the preview cannot drift
 * from the downloaded file.
 *
 * Deliberately WHITE in both themes: this is a printable document, and
 * rendering it dark would misrepresent the PDF. It sits on the dark surround
 * like a page on a desk.
 *
 * Honest limitation: this is an HTML re-render of the same structured_data
 * the exporter uses, not a rasterised PDF. fpdf2 paginates differently, so
 * line wrapping and page breaks will differ; content and order will not.
 */

const FONT_STACK: Record<DesignTokens["font"], string> = {
  sans: "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
  serif: "Georgia, 'Times New Roman', Times, serif",
};

/** Mirrors _DENSITY in export_service.py: section gap and line height. */
const DENSITY: Record<DesignTokens["density"], { gap: string; leading: string }> = {
  dense: { gap: "mt-2", leading: "leading-snug" },
  normal: { gap: "mt-3", leading: "leading-normal" },
  airy: { gap: "mt-4", leading: "leading-relaxed" },
};

function SectionHeading({ title, tokens }: { title: string; tokens: DesignTokens }) {
  const label = tokens.caps ? title.toUpperCase() : title;
  const accent = tokens.accent || undefined;

  const base = cn(
    "text-[9px] font-bold",
    tokens.caps && "tracking-[0.12em]",
    !tokens.accent && "text-paper-ink",
  );

  if (tokens.heading === "sidebar") {
    return (
      <h2
        className={cn(base, "border-l-2 pl-2")}
        style={{ color: accent, borderColor: accent ?? "#111111" }}
      >
        {label}
      </h2>
    );
  }

  if (tokens.heading === "boxed") {
    return (
      <h2
        className={cn(base, "inline-block border px-2 py-0.5")}
        style={{ color: accent, borderColor: accent ?? "#c9c9c9" }}
      >
        {label}
      </h2>
    );
  }

  if (tokens.heading === "rule" || tokens.heading === "underline") {
    return (
      <h2
        className={cn(base, "border-b pb-0.5")}
        style={{
          color: accent,
          // "rule" tints the line with the accent; "underline" stays neutral,
          // which is what separates Modern from Classic.
          borderColor: tokens.heading === "rule" && accent ? accent : "var(--paper-line)",
        }}
      >
        {label}
      </h2>
    );
  }

  return (
    <h2 className={base} style={{ color: accent }}>
      {label}
    </h2>
  );
}

function Section({
  title,
  tokens,
  children,
}: {
  title: string;
  tokens: DesignTokens;
  children: React.ReactNode;
}) {
  return (
    <section className={DENSITY[tokens.density].gap}>
      <SectionHeading title={title} tokens={tokens} />
      <div className="mt-1">{children}</div>
    </section>
  );
}

/**
 * One experience / project / education entry.
 *
 * Dates sit flush right rather than trailing the title — that is what makes a
 * printed resume scannable by date, and it mirrors entry_header() in the
 * exporter.
 */
function Entry({
  primary,
  secondary,
  dates,
}: {
  primary: string;
  secondary?: string;
  dates?: string;
}) {
  return (
    <>
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-[10.5px] font-semibold">{primary}</p>
        {dates && <p className="shrink-0 text-[9px] text-paper-muted">{dates}</p>}
      </div>
      {secondary && <p className="text-[9.5px] italic text-paper-muted">{secondary}</p>}
    </>
  );
}

export function ResumePreview({
  data,
  tokens: tokensProp,
  zoom = 100,
  className,
}: {
  data: StructuredData;
  /** From the chosen TemplateOut. Falls back to the backend's defaults. */
  tokens?: DesignTokens;
  zoom?: number;
  className?: string;
}) {
  const tokens: DesignTokens = { ...DEFAULT_DESIGN_TOKENS, ...(tokensProp ?? {}) };
  const { contact, summary, experience, projects, education, skills } = data;
  const { leading } = DENSITY[tokens.density];
  const centred = tokens.name_align === "center";
  const accent = tokens.accent || undefined;

  const isEmpty =
    !contact.name &&
    !summary.text &&
    experience.length === 0 &&
    projects.length === 0 &&
    education.length === 0 &&
    skills.length === 0;

  const detailLines = [contact.email, contact.phone, contact.location].filter(Boolean);
  const bullet = "text-[10px] " + leading;
  // Clamp to the same 1..3 range the exporter enforces.
  const columns = Math.max(1, Math.min(Number(tokens.skill_columns) || 1, 3));

  return (
    <div className={cn("flex justify-center", className)}>
      <div
        // A4 is 1:1.414. Fixed width + min-height keeps the proportion honest
        // rather than letting content define the page shape.
        className="w-[520px] min-h-[735px] rounded-sm bg-paper px-9 py-8 text-paper-ink shadow-2xl"
        style={{
          fontFamily: FONT_STACK[tokens.font],
          transform: `scale(${zoom / 100})`,
          transformOrigin: "top center",
        }}
      >
        {isEmpty ? (
          <div className="grid h-[600px] place-items-center text-center">
            <div>
              <p className="text-sm font-medium text-paper-ink">Nothing to preview yet</p>
              <p className="mt-1 text-xs text-paper-muted">
                Import a resume to see it rendered here.
              </p>
            </div>
          </div>
        ) : (
          <>
            {/* header */}
            {tokens.header === "split" && !centred ? (
              <header className="flex items-start justify-between gap-6">
                <div className="min-w-0">
                  <h1
                    className={cn(
                      "font-bold leading-tight",
                      tokens.density === "airy" ? "text-[24px]" : "text-[21px]",
                    )}
                    style={{ color: accent }}
                  >
                    {contact.name || "Unnamed"}
                  </h1>
                  {contact.headline && (
                    <p className="mt-0.5 text-[11px] text-paper-muted">{contact.headline}</p>
                  )}
                  {contact.links.length > 0 && (
                    <p className="mt-0.5 text-[9px] text-paper-muted">
                      {contact.links.join("  ·  ")}
                    </p>
                  )}
                </div>
                <div className="shrink-0 text-right">
                  {detailLines.map((line, i) => (
                    <p key={i} className="text-[9px] leading-snug text-paper-muted">
                      {line}
                    </p>
                  ))}
                </div>
              </header>
            ) : (
              <header className={centred ? "text-center" : undefined}>
                <h1
                  className={cn(
                    "font-bold leading-tight",
                    tokens.density === "airy" ? "text-[24px]" : "text-[21px]",
                  )}
                  style={{ color: accent }}
                >
                  {contact.name || "Unnamed"}
                </h1>
                {contact.headline && (
                  <p className="mt-0.5 text-[11px] text-paper-muted">{contact.headline}</p>
                )}
                {detailLines.length > 0 && (
                  <p className="mt-1 text-[9px] text-paper-muted">
                    {detailLines.join("  ·  ")}
                  </p>
                )}
                {contact.links.length > 0 && (
                  <p className="mt-0.5 text-[9px] text-paper-muted">
                    {contact.links.join("  ·  ")}
                  </p>
                )}
              </header>
            )}

            {tokens.divider && <hr className="mt-2 border-paper-line" />}

            {summary.text && (
              <Section title="Summary" tokens={tokens}>
                <p className={bullet}>{summary.text}</p>
              </Section>
            )}

            {experience.length > 0 && (
              <Section title="Experience" tokens={tokens}>
                {experience.map((job, i) => (
                  <div key={i} className={i > 0 ? "mt-2" : undefined}>
                    <Entry
                      dates={dateLabel(job)}
                      primary={
                        tokens.entry === "inline"
                          ? [job.role, job.company].filter(Boolean).join(" · ")
                          : job.role || job.company
                      }
                      secondary={
                        tokens.entry === "inline" ? undefined : job.role ? job.company : undefined
                      }
                    />
                    <ul className="mt-0.5 list-disc space-y-0.5 pl-3.5">
                      {job.bullets.map((b, j) => (
                        <li key={j} className={bullet}>
                          {b}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </Section>
            )}

            {projects.length > 0 && (
              <Section title="Projects" tokens={tokens}>
                {projects.map((p, i) => (
                  <div key={i} className={i > 0 ? "mt-2" : undefined}>
                    {/* project entries carry `name`, not company/role */}
                    <Entry primary={p.name} />
                    <ul className="mt-0.5 list-disc space-y-0.5 pl-3.5">
                      {p.bullets.map((b, j) => (
                        <li key={j} className={bullet}>
                          {b}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </Section>
            )}

            {education.length > 0 && (
              <Section title="Education" tokens={tokens}>
                {education.map((e, i) => (
                  <div key={i} className={i > 0 ? "mt-1.5" : undefined}>
                    <Entry
                      dates={dateLabel(e)}
                      primary={e.degree || e.school}
                      secondary={e.degree ? e.school : undefined}
                    />
                  </div>
                ))}
              </Section>
            )}

            {skills.length > 0 && (
              <Section title="Skills" tokens={tokens}>
                {skills.map((g, i) => (
                  <div key={i} className={i > 0 ? "mt-1" : undefined}>
                    {g.label && (
                      <p className="text-[9.5px] font-semibold">{g.label}</p>
                    )}
                    {columns === 1 ? (
                      <p className={bullet}>{g.items.join(", ")}</p>
                    ) : (
                      <ul
                        className="list-disc pl-3.5"
                        // Column-fill matches the exporter, which fills down
                        // each column rather than across rows.
                        style={{ columnCount: columns, columnGap: "1rem" }}
                      >
                        {g.items.map((item, j) => (
                          <li key={j} className={bullet}>
                            {item}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </Section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
