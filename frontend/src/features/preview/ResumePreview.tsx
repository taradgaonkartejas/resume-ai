import {
  DEFAULT_DESIGN_TOKENS,
  type DesignTokens,
  type ExtraEntry,
  type StructuredData,
} from "@/services";
import { dateLabel } from "@/lib/dates";
import { educationLines, experienceLines } from "@/lib/entries";
import { hrefFor, labelLinks } from "@/lib/links";
import { cn } from "cn";
import { useMemo } from "react";
import {
  EDUCATION_FIELDS,
  SUMMARY_REF,
  educationRef,
  experienceBulletRef,
  isChanged,
  projectBulletRef,
  skillsRef,
} from "@/features/preview/changeRefs";

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

/**
 * A changed range.
 *
 * `--color-change` (green), never yellow -- the palette forbids yellow, and
 * on a white page it reads as a highlighter pen rather than a diff. Rendered
 * as a real <mark> so the meaning survives for screen readers and for print,
 * with the browser's default background overridden because it IS yellow.
 *
 * Green is also the Accept colour, which is a real risk: a green bullet can
 * be misread as "accepted" rather than "changed". Two things keep them
 * apart -- accept is a SOLID green fill behind a white label on a control,
 * this is a PALE green wash behind body text in the document, and the two
 * never appear on the same surface. The border is what stops the pale wash
 * dissolving into the page at 80% zoom.
 */
function Marked({ on, children }: { on: boolean; children: React.ReactNode }) {
  if (!on) return <>{children}</>;
  return (
    <mark
      className="rounded-[3px] border border-change-border bg-change px-1
                 text-on-change"
    >
      {children}
    </mark>
  );
}

/**
 * A dashed ring around a block that CONTAINS a change.
 *
 * Two treatments carry two different meanings and must not be collapsed into
 * one: the filled <mark> is "this text changed", the dashed ring is "there is
 * a change somewhere in here". The ring is what lets a reader find the change
 * on a dense page before they can read any of it -- scanning for a thin green
 * fill in a wall of bullets is exactly the problem it solves.
 *
 * `outline` rather than `border` deliberately: a border would reflow the
 * document by its own width, so toggling "Show changes" would shift the page
 * under the reader's eye.
 */
function ChangedBlock({
  on,
  children,
}: {
  on: boolean;
  children: React.ReactNode;
}) {
  if (!on) return <>{children}</>;
  return (
    <div
      className="rounded-[4px] outline-1 outline-offset-[3px] outline-dashed
                 outline-change-outline"
    >
      {children}
    </div>
  );
}

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

/**
 * One extras entry as a single display line.
 *
 * Mirrors resume_ops.extra_entry_line so the preview, the PDF, the DOCX and the
 * TXT can never drift into four renderings of the same data.
 */
function extraLine(entry: ExtraEntry): string {
  const head = [entry.primary, entry.secondary].filter(Boolean).join(" \u2014 ");
  const date = (entry.date || "").trim();
  const detail = (entry.detail || "").trim();
  let out = head;
  if (date) out = out ? `${out} (${date})` : date;
  if (detail) out = out ? `${out} \u00b7 ${detail}` : detail;
  return out.trim();
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
  marked = false,
}: {
  primary: string;
  secondary?: string;
  dates?: string;
  /** Mark the whole entry -- used where several addressable fields are
   *  merged into one display string. */
  marked?: boolean;
}) {
  return (
    <>
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-[10.5px] font-semibold">
          <Marked on={marked}>{primary}</Marked>
        </p>
        {dates && <p className="shrink-0 text-[9px] text-paper-muted">{dates}</p>}
      </div>
      {secondary && (
        <p className="text-[9.5px] italic text-paper-muted">
          <Marked on={marked}>{secondary}</Marked>
        </p>
      )}
    </>
  );
}

/**
 * Profile links as labels, not URLs, and actually clickable.
 *
 * Both call sites previously rendered `links.join("  ·  ")` — raw URLs, inert
 * text. The resume this was built for writes "LinkedIn" over a hyperlink, and
 * the preview exists to show what the export will look like, so it has to
 * agree with the PDF and DOCX renderers.
 */
function ProfileLinks({ links }: { links: string[] }) {
  return (
    <>
      {labelLinks(links.filter(Boolean)).map(({ url, label }, i) => (
        <span key={url + i}>
          {i > 0 && "  ·  "}
          <a
            href={hrefFor(url)}
            target="_blank"
            rel="noopener noreferrer"
            title={url}
            className="underline decoration-dotted underline-offset-2"
          >
            {label}
          </a>
        </span>
      ))}
    </>
  );
}

export function ResumePreview({
  data,
  tokens: tokensProp,
  zoom = 100,
  className,
  changedRefs,
  showChanges = false,
}: {
  data: StructuredData;
  /** From the chosen TemplateOut. Falls back to the backend's defaults. */
  tokens?: DesignTokens;
  zoom?: number;
  className?: string;
  /** Target refs to mark as changed. Same spelling as resume_ops. */
  changedRefs?: readonly string[];
  /** The "Show Changes" toggle. Off means no marks at all. */
  showChanges?: boolean;
}) {
  const tokens: DesignTokens = { ...DEFAULT_DESIGN_TOKENS, ...(tokensProp ?? {}) };
  // Built once per render rather than per bullet: a 30-bullet resume would
  // otherwise rebuild the Set 30 times.
  const marked = useMemo(() => new Set(changedRefs ?? []), [changedRefs]);
  const mark = (ref: string) => isChanged(ref, marked, showChanges);
  const { contact, summary, experience, projects, education, skills } = data;
  // Sections with at least one entry that renders to something. Mirrors
  // resume_ops.visible_extras: an empty heading is worse than no heading,
  // on screen and for an ATS parser.
  const extras = (data.extras ?? [])
    .map((s) => ({ ...s, entries: s.entries.filter(extraLine) }))
    .filter((s) => s.entries.length > 0);
  const { leading } = DENSITY[tokens.density];
  const centred = tokens.name_align === "center";
  const accent = tokens.accent || undefined;

  const isEmpty =
    !contact.name &&
    !summary.text &&
    experience.length === 0 &&
    projects.length === 0 &&
    education.length === 0 &&
    skills.length === 0 &&
    extras.length === 0;

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
                      <ProfileLinks links={contact.links} />
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
                    <ProfileLinks links={contact.links} />
                  </p>
                )}
              </header>
            )}

            {tokens.divider && <hr className="mt-2 border-paper-line" />}

            {summary.text && (
              <Section title="Summary" tokens={tokens}>
                <ChangedBlock on={mark(SUMMARY_REF)}>
                  <p className={bullet}>
                    <Marked on={mark(SUMMARY_REF)}>{summary.text}</Marked>
                  </p>
                </ChangedBlock>
              </Section>
            )}

            {experience.length > 0 && (
              <Section title="Experience" tokens={tokens}>
                {experience.map((job, i) => (
                  <ChangedBlock
                    key={i}
                    on={job.bullets.some((_, j) => mark(experienceBulletRef(i, j)))}
                  >
                  <div className={i > 0 ? "mt-2" : undefined}>
                    <Entry
                      dates={dateLabel(job)}
                      primary={experienceLines(job, tokens.entry === "inline")[0]}
                      secondary={
                        experienceLines(job, tokens.entry === "inline")[1] || undefined
                      }
                    />
                    <ul className="mt-0.5 list-disc space-y-0.5 pl-3.5">
                      {job.bullets.map((b, j) => (
                        <li key={j} className={bullet}>
                          <Marked on={mark(experienceBulletRef(i, j))}>{b}</Marked>
                        </li>
                      ))}
                    </ul>
                  </div>
                  </ChangedBlock>
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
                          <Marked on={mark(projectBulletRef(i, j))}>{b}</Marked>
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
                    {/* educationLines() merges several addressable fields
                        into two display strings, so a per-field highlight
                        would have to re-derive which substring came from
                        which field. The entry is marked as a whole if ANY
                        of its fields changed -- coarser, but it never
                        points at the wrong text. */}
                    <Entry
                      dates={dateLabel(e)}
                      primary={educationLines(e)[0]}
                      secondary={educationLines(e)[1] || undefined}
                      marked={EDUCATION_FIELDS.some((f) => mark(educationRef(i, f)))}
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
                      <p className={bullet}>
                        {/* skills.N.items addresses the whole group, so the
                            whole joined line is the changed range */}
                        <Marked on={mark(skillsRef(i))}>{g.items.join(", ")}</Marked>
                      </p>
                    ) : (
                      <ul
                        className="list-disc pl-3.5"
                        // Column-fill matches the exporter, which fills down
                        // each column rather than across rows.
                        style={{ columnCount: columns, columnGap: "1rem" }}
                      >
                        {g.items.map((item, j) => (
                          <li key={j} className={bullet}>
                            <Marked on={mark(skillsRef(i))}>{item}</Marked>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </Section>
            )}

            {extras.map((section, i) => (
              <Section key={i} title={section.title} tokens={tokens}>
                {section.entries.map((entry, j) => (
                  <p key={j} className={bullet}>
                    {extraLine(entry)}
                  </p>
                ))}
              </Section>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
