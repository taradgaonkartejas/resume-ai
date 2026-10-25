import {
  DEFAULT_DESIGN_TOKENS,
  type DesignTokens,
  type ExtraEntry,
  type StructuredData,
} from "@/services";
import { dateLabel } from "@/lib/dates";
import { educationCells, experienceLines } from "@/lib/entries";
import { hrefFor, linkLabel } from "@/lib/links";
import { Icon } from "@/lib/icons";
import { contactIcon, headingIcon, linkIcon } from "@/lib/icons-map";
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
const DENSITY: Record<
  DesignTokens["density"],
  { gap: string; leading: string }
> = {
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

function SectionHeading({
  title,
  tokens,
}: {
  title: string;
  tokens: DesignTokens;
}) {
  const label = tokens.caps ? title.toUpperCase() : title;
  const accent = tokens.accent || undefined;

  const base = cn(
    "text-[9px] font-bold",
    tokens.caps && "tracking-[0.12em]",
    !tokens.accent && "text-paper-ink",
  );

  // Mirrors the PDF: vectors tinted with the accent, indenting the label --
  // and skipped for sidebar/boxed, where the drawn bar owns the slot.
  const icon = tokens.icons &&
    ["rule", "underline", "plain"].includes(tokens.heading) && (
      <Icon
        name={headingIcon(title)}
        size={9.5}
        color={accent ?? "#3A3A3A"}
        className="mr-[5px]"
      />
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
          borderColor:
            tokens.heading === "rule" && accent ? accent : "var(--paper-line)",
        }}
      >
        {icon}
        {label}
      </h2>
    );
  }

  return (
    <h2 className={base} style={{ color: accent }}>
      {icon}
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
  const head = [entry.primary, entry.secondary]
    .filter(Boolean)
    .join(" \u2014 ");
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
  italicDates = false,
  marked = false,
}: {
  primary: string;
  secondary?: string;
  dates?: string;
  italicDates?: boolean;
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
        {dates && (
          <p
            className={cn(
              "shrink-0 text-[9px] text-paper-muted",
              italicDates && "italic",
            )}
          >
            {dates}
          </p>
        )}
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
 * Profile links as short URLs, and actually clickable.
 *
 * Both call sites once rendered `links.join("  ·  ")` — raw URLs, inert text.
 * They then showed a bare label ("LinkedIn"), which reads well on screen and
 * is useless on paper. Now: the shortened address, hyperlinked. The preview
 * exists to show what the export will look like, so it agrees with the PDF.
 */
function ProfileLinks({
  links,
  stacked = false,
}: {
  links: string[];
  /** One per line, for the split header's right column. */
  stacked?: boolean;
}) {
  return (
    <>
      {links.filter(Boolean).map((url, i) => (
        <span key={url + i} className={stacked ? "block" : undefined}>
          {!stacked && i > 0 && "  ·  "}
          <a
            href={hrefFor(url)}
            target="_blank"
            rel="noopener noreferrer"
            title={url}
            className="underline decoration-dotted underline-offset-2"
          >
            {linkLabel(url)}
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
  const tokens: DesignTokens = {
    ...DEFAULT_DESIGN_TOKENS,
    ...(tokensProp ?? {}),
  };
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

  const detailLines = [contact.email, contact.phone, contact.location].filter(
    Boolean,
  );
  const bullet = "text-[10px] " + leading;

  // Sidebar templates put education and extras in a right rail. Skills
  // stays directly under Summary in every template (incl. the sidebar:
  // skills-below-summary ruling, 2026-10-25). Experience never moves: it
  // is the longest section and the one a reader scans first, so it keeps
  // the wide column. Mirrors MAIN/RAIL in export_service._render_pdf.
  const layout = tokens.layout ?? "single";
  const mainSections = (
    <>
      {summary.text && (
        <Section title="Summary" tokens={tokens}>
          <ChangedBlock on={mark(SUMMARY_REF)}>
            <p className={bullet}>
              <Marked on={mark(SUMMARY_REF)}>{summary.text}</Marked>
            </p>
          </ChangedBlock>
        </Section>
      )}
      {skills.length > 0 && (
        <Section title="Skills" tokens={tokens}>
          {/* One wrapped line per group -- "Languages: Java, Python".
              A bulleted multi-column grid was costing roughly half the
              section's height and, in the PDF, was a text-extraction
              hazard. Label and items share one paragraph so the line
              wraps as a unit. */}
          {skills.map((g, i) => (
            <p key={i} className={cn(bullet, i > 0 && "mt-0.5")}>
              {g.label && <span className="font-semibold">{g.label}: </span>}
              {/* skills.N.items addresses the whole group, so the
                  whole joined line is the changed range */}
              <Marked on={mark(skillsRef(i))}>{g.items.join(", ")}</Marked>
            </p>
          ))}
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
                {(() => {
                  // Condensed roles collapse to one bold line -- this
                  // template's own separator, exactly like the PDF.
                  const [primary, secondary] = job.condensed
                    ? experienceLines(job, true, "role", tokens.entry_sep)
                    : experienceLines(
                        job,
                        tokens.entry === "inline",
                        tokens.entry_lead,
                        tokens.entry_sep,
                      );
                  return (
                    <>
                      <Entry
                        dates={dateLabel(job)}
                        italicDates={tokens.dates_style === "italic"}
                        primary={primary}
                        secondary={secondary || undefined}
                      />
                      {job.descriptor && (
                        <p className="mt-0.5 text-[9px] italic text-paper-muted">
                          {job.descriptor}
                        </p>
                      )}
                      {!job.condensed && (
                        <ul className="mt-0.5 list-disc space-y-0.5 pl-3.5">
                          {job.bullets.map((b, j) => (
                            <li key={j} className={bullet}>
                              <Marked on={mark(experienceBulletRef(i, j))}>
                                {b}
                              </Marked>
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  );
                })()}
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
    </>
  );
  const railSections = (
    <>
      {/* Mirrors sec_contact() in export_service: a sidebar's contact
          details and links lead the right rail, flush right, before
          skills. Kept out of mainSections for exactly the reason the PDF
          keeps them out of the header: one full-width contact line is
          enough to hide the gutter from every column detector. */}
      {layout === "sidebar" &&
        (detailLines.length > 0 || contact.links.length > 0) && (
          <div className="text-right text-[9px] leading-snug text-paper-muted">
            {detailLines.map((line, i) => (
              <p key={i}>{line}</p>
            ))}
            {contact.links.length > 0 && (
              <p>
                <ProfileLinks links={contact.links} stacked />
              </p>
            )}
          </div>
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
              {(() => {
                const [primary, secondary, dates] = educationCells(
                  e,
                  tokens.edu === "inline",
                  tokens.edu_lead === "school",
                );
                return (
                  <Entry
                    dates={dates}
                    primary={primary}
                    italicDates={tokens.dates_style === "italic"}
                    secondary={secondary || undefined}
                    marked={EDUCATION_FIELDS.some((f) =>
                      mark(educationRef(i, f)),
                    )}
                  />
                );
              })()}
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
  );

  return (
    <div className={cn("flex justify-center", className)}>
      <div
        // A4 is 1:1.414. Fixed width + min-height keeps the proportion honest
        // rather than letting content define the page shape.
        className="w-[520px] min-h-[735px] rounded-sm bg-paper px-9 py-7 text-paper-ink shadow-2xl"
        style={{
          fontFamily: FONT_STACK[tokens.font],
          transform: `scale(${zoom / 100})`,
          transformOrigin: "top center",
        }}
      >
        {isEmpty ? (
          <div className="grid h-[600px] place-items-center text-center">
            <div>
              <p className="text-sm font-medium text-paper-ink">
                Nothing to preview yet
              </p>
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
                    <p className="mt-0.5 text-[11px] text-paper-muted">
                      {contact.headline}
                    </p>
                  )}
                </div>
                {/* Contact AND links share the right column, matching the
                    PDF: the exporter stacks the profile links under the
                    detail rows in the same right-hand block. */}
                <div className="shrink-0 text-right">
                  {detailLines.map((line, i) => (
                    <p
                      key={i}
                      className="text-[9px] leading-snug text-paper-muted"
                    >
                      {line}
                    </p>
                  ))}
                  {contact.links.length > 0 && (
                    <p className="text-[9px] leading-snug text-paper-muted">
                      <ProfileLinks links={contact.links} stacked />
                    </p>
                  )}
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
                  <p className="mt-0.5 text-[11px] text-paper-muted">
                    {contact.headline}
                  </p>
                )}
                {/* In the PDF, contact and links share ONE row, laid out by
                    hand because a link cell cannot sit inside a multi_cell.
                    Here flexbox gives the same result for free. A sidebar's
                    contact block lives in the rail instead, as it does in
                    the PDF. */}
                {layout !== "sidebar" &&
                  (detailLines.length > 0 || contact.links.length > 0) &&
                  (tokens.icons ? (
                    /* The iconed twin of the row below: one icon+text pair
                       per contact item and per link, " · " between pairs --
                       same order and separators as _draw_icon_row. */
                    <p className="mt-1 flex flex-wrap items-center gap-x-[3px] gap-y-[2px] text-[9px] leading-snug text-paper-muted">
                      {[
                        ...detailLines.map((line) => ({
                          key: line,
                          icon: contactIcon(line),
                          body: line,
                          href: null as string | null,
                        })),
                        ...contact.links.filter(Boolean).map((url) => ({
                          key: url,
                          icon: linkIcon(url),
                          body: linkLabel(url),
                          href: hrefFor(url),
                        })),
                      ].map((item, i) => (
                        <span key={item.key + i} className="inline-flex items-center gap-[3px]">
                          {i > 0 && <span className="mx-[2px]">·</span>}
                          <Icon name={item.icon} size={8.5} color={tokens.accent || "#3A3A3A"} />
                          {item.href ? (
                            <a
                              href={item.href}
                              target="_blank"
                              rel="noopener noreferrer"
                              title={item.key}
                              className="underline decoration-dotted underline-offset-2"
                            >
                              {item.body}
                            </a>
                          ) : (
                            item.body
                          )}
                        </span>
                      ))}
                    </p>
                  ) : (
                    <p className="mt-1 text-[9px] leading-snug text-paper-muted">
                      {detailLines.join("  ·  ")}
                      {detailLines.length > 0 && contact.links.length > 0 &&
                        "  ·  "}
                      <ProfileLinks links={contact.links} />
                    </p>
                  ))}
              </header>
            )}

            {tokens.divider && <hr className="mt-2 border-paper-line" />}

            {layout === "sidebar" ? (
              /* Two columns. The gutter is not decoration -- the PDF's
                 is what lets a text extractor find the split, and the
                 preview has to show the same proportions. */
              <div className="flex gap-5">
                <div className="min-w-0 flex-1">{mainSections}</div>
                <aside className="w-[30%] shrink-0">{railSections}</aside>
              </div>
            ) : (
              <>
                {mainSections}
                {railSections}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
