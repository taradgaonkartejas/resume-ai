import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BulletRow, DateRangeField, Field, TextAreaField, TipsPanel } from "./fields";
import { dateLabel } from "@/lib/dates";
import { ExtrasEditor } from "./ExtrasEditor";
import type { StepKey, StructuredData } from "@/services";

/**
 * The editable surface for each step.
 *
 * Steps 1-4 edit the scored sections: contact, summary, experience, and the
 * structural fields behind Format. The fifth step, "extras", edits the optional
 * sections and is NOT scored — it renders only ExtrasEditor, with no findings
 * or recommendation cards, because the extras category never emits any.
 *
 * Every section here is rendered end to end: preview, PDF, DOCX and TXT.
 * Shipping a field the exports silently drop would be its own kind of lie.
 */

type Props = {
  data: StructuredData;
  onChange: (next: StructuredData) => void;
  /** target_ref of the field the user asked to fix; drives autofocus. */
  focusRef: string | null;
};

const clone = (d: StructuredData): StructuredData =>
  JSON.parse(JSON.stringify(d)) as StructuredData;

export function StepSections({ step, ...props }: Props & { step: StepKey }) {
  if (step === "contact") return <ContactSection {...props} />;
  if (step === "summary") return <SummarySection {...props} />;
  if (step === "experience") return <ExperienceSection {...props} />;
  if (step === "extras") {
    return <ExtrasEditor data={props.data} onChange={props.onChange} />;
  }
  return <FormatSection {...props} />;
}

/* ------------------------------------------------------------- contact --- */
function ContactSection({ data, onChange, focusRef }: Props) {
  /** Typed setter: only the string-valued contact keys, so no cast is needed. */
  const set = (
    key: "name" | "headline" | "email" | "phone" | "location",
    value: string,
  ) => {
    const next = clone(data);
    next.contact[key] = value;
    onChange(next);
  };

  const links = data.contact.links ?? [];

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Full name" value={data.contact.name ?? ""}
               onChange={(v) => set("name", v)} placeholder="Priya Sharma"
               highlight={focusRef === "contact.name"} id="f-contact.name" />
        <Field label="Professional headline" value={data.contact.headline ?? ""}
               onChange={(v) => set("headline", v)}
               placeholder="Site Reliability Engineer"
               id="f-contact.headline" />
        <Field label="Email" type="email" value={data.contact.email ?? ""}
               onChange={(v) => set("email", v)} placeholder="you@example.com"
               highlight={focusRef === "contact.email"} id="f-contact.email" />
        <Field label="Phone" value={data.contact.phone ?? ""}
               onChange={(v) => set("phone", v)} placeholder="+91 90000 00000"
               highlight={focusRef === "contact.phone"} id="f-contact.phone" />
        <Field label="Location" value={data.contact.location ?? ""}
               onChange={(v) => set("location", v)} placeholder="Pune, India"
               highlight={focusRef === "contact.location"}
               id="f-contact.location" />
      </div>

      <fieldset id="f-contact.links">
        <legend className="mb-1 text-xs font-medium text-content-muted">
          Profile links
        </legend>
        <div className="space-y-2">
          {links.map((link, i) => (
            <div key={i} className="flex gap-2">
              <input
                value={link}
                onChange={(e) => {
                  const next = clone(data);
                  next.contact.links[i] = e.target.value;
                  onChange(next);
                }}
                placeholder="github.com/you"
                className="w-full rounded-lg border border-line-soft bg-surface-1 px-3
                           py-2 text-sm outline-none focus-visible:border-brand
                           focus-visible:ring-2 focus-visible:ring-brand/40"
              />
              <button
                type="button"
                onClick={() => {
                  const next = clone(data);
                  next.contact.links.splice(i, 1);
                  onChange(next);
                }}
                aria-label={`Remove link ${i + 1}`}
                className="rounded-md px-2 text-content-muted hover:text-reject-text"
              >
                ×
              </button>
            </div>
          ))}
          <Button
            variant="outline" size="sm"
            onClick={() => {
              const next = clone(data);
              next.contact.links = [...(next.contact.links ?? []), ""];
              onChange(next);
            }}
          >
            <Plus className="size-3.5" aria-hidden /> Add link
          </Button>
        </div>
      </fieldset>
    </div>
  );
}

/* ------------------------------------------------------------- summary --- */
function SummarySection({ data, onChange, focusRef }: Props) {
  return (
    <div className="space-y-3">
    <TextAreaField
      label="Professional summary"
      id="f-summary.text"
      hint="aim for 30–60"
      rows={5}
      value={data.summary?.text ?? ""}
      highlight={focusRef === "summary.text"}
      onChange={(v) => {
        const next = clone(data);
        next.summary = { ...next.summary, text: v };
        onChange(next);
      }}
      placeholder="Site reliability engineer with six years running production infrastructure. Led a migration that cut deploy time by 65%…"
    />
    <TipsPanel tips={[
      "Lead with the role you want, not the one you have.",
      "Name one number: team size, users served, latency cut, revenue moved.",
      "Three lines is plenty. This is a hook, not a biography.",
    ]} />
    </div>
  );
}

/* ---------------------------------------------------------- experience --- */
function ExperienceSection({ data, onChange, focusRef }: Props) {
  const entries = data.experience ?? [];

  const mutate = (fn: (d: StructuredData) => void) => {
    const next = clone(data);
    fn(next);
    onChange(next);
  };

  return (
    <div className="space-y-4">
      {entries.map((entry, i) => (
        <div key={i} className="rounded-xl border border-line-soft bg-surface-2 p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Company" value={entry.company ?? ""}
                   onChange={(v) => mutate((d) => { d.experience[i].company = v; })} />
            <Field label="Role" value={entry.role ?? ""}
                   onChange={(v) => mutate((d) => { d.experience[i].role = v; })} />
            <Field label="Location" value={entry.location ?? ""}
                   placeholder="Pune, India"
                   onChange={(v) => mutate((d) => { d.experience[i].location = v; })} />
          </div>

          <div className="mt-3">
            <DateRangeField
              id={`f-exp_${i}`}
              startDate={entry.start_date ?? ""}
              endDate={entry.end_date ?? ""}
              current={Boolean(entry.current)}
              rawDates={entry.raw_dates}
              highlight={focusRef === `exp_${i}`}
              onChange={(patch) => mutate((d) => {
                Object.assign(d.experience[i], patch);
              })}
            />
            <p className="mt-1.5 text-[11px] text-content-muted">
              Shows as <span className="text-content">{dateLabel(entry) || "—"}</span>
            </p>
          </div>

          <p className="mt-3 mb-1.5 text-xs font-medium text-content-muted">
            Achievements
          </p>
          <div className="space-y-2">
            {(entry.bullets ?? []).map((bullet, j) => (
              <BulletRow
                key={j}
                id={`f-exp_${i}.bullet_${j}`}
                value={bullet}
                focus={focusRef === `exp_${i}.bullet_${j}`}
                highlight={focusRef === `exp_${i}.bullet_${j}`}
                onChange={(v) => mutate((d) => { d.experience[i].bullets[j] = v; })}
                onRemove={() => mutate((d) => { d.experience[i].bullets.splice(j, 1); })}
              />
            ))}
            <Button
              variant="outline" size="sm"
              onClick={() => mutate((d) => {
                d.experience[i].bullets = [...(d.experience[i].bullets ?? []), ""];
              })}
            >
              <Plus className="size-3.5" aria-hidden /> Add achievement
            </Button>
          </div>
        </div>
      ))}

      <TipsPanel tips={[
        "Start each bullet with a verb: Led, Built, Reduced, Migrated.",
        "Quantify the outcome, not the task — \"cut deploy time 65%\" beats \"responsible for deploys\".",
        "Keep bullets under about 30 words so they actually get read.",
      ]} />

      <Button
        variant="outline"
        onClick={() => mutate((d) => {
          d.experience = [...(d.experience ?? []), {
            company: "", role: "", location: "",
            start_date: "", end_date: "", current: false,
            dates: "", bullets: [""],
          }];
        })}
      >
        <Plus className="size-4" aria-hidden /> Add role
      </Button>
    </div>
  );
}

/* -------------------------------------------------------------- format --- */
function FormatSection({ data, onChange }: Props) {
  const mutate = (fn: (d: StructuredData) => void) => {
    const next = clone(data);
    fn(next);
    onChange(next);
  };

  const skills = data.skills ?? [];
  const education = data.education ?? [];

  return (
    <div className="space-y-5">
      <section id="f-skills">
        <p className="mb-2 text-xs font-medium text-content-muted">
          Skills — grouped by category
        </p>
        <div className="space-y-3">
          {skills.map((group, i) => (
            <div key={i} className="rounded-xl border border-line-soft bg-surface-2 p-3">
              <div className="flex items-center gap-2">
                <input
                  value={group.label ?? ""}
                  placeholder="Category"
                  onChange={(e) => mutate((d) => { d.skills[i].label = e.target.value; })}
                  className="flex-1 rounded-md bg-transparent px-1 py-0.5 text-sm
                             font-medium outline-none focus-visible:bg-surface-3"
                />
                <button
                  type="button"
                  onClick={() => mutate((d) => { d.skills.splice(i, 1); })}
                  aria-label={`Remove ${group.label || "group"}`}
                  className="rounded-md px-2 text-content-muted hover:text-reject-text"
                >
                  ×
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(group.items ?? []).map((item, j) => (
                  <span
                    key={j}
                    className="inline-flex items-center gap-1 rounded-full bg-surface-3
                               py-0.5 pl-2.5 pr-1 text-xs"
                  >
                    {item}
                    <button
                      type="button"
                      onClick={() => mutate((d) => { d.skills[i].items.splice(j, 1); })}
                      aria-label={`Remove ${item}`}
                      className="rounded-full px-1 text-content-muted hover:text-reject-text"
                    >
                      ×
                    </button>
                  </span>
                ))}
                <input
                  placeholder="Add skill…"
                  onKeyDown={(e) => {
                    if (e.key !== "Enter") return;
                    const value = e.currentTarget.value.trim();
                    if (!value) return;
                    e.preventDefault();
                    e.currentTarget.value = "";
                    mutate((d) => {
                      d.skills[i].items = [...(d.skills[i].items ?? []), value];
                    });
                  }}
                  className="min-w-24 flex-1 rounded-full bg-transparent px-2 py-0.5
                             text-xs outline-none placeholder:text-content-muted
                             focus-visible:bg-surface-3"
                />
              </div>
            </div>
          ))}
          <Button
            variant="outline" size="sm"
            onClick={() => mutate((d) => {
              d.skills = [...(d.skills ?? []), { label: "New group", items: [] }];
            })}
          >
            <Plus className="size-3.5" aria-hidden /> Add skill group
          </Button>
        </div>
      </section>

      <section id="f-education">
        <p className="mb-2 text-xs font-medium text-content-muted">Education</p>
        <div className="space-y-3">
          {education.map((entry, i) => (
            <div key={i} className="rounded-xl border border-line-soft bg-surface-2 p-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="School" value={entry.school ?? ""}
                       onChange={(v) => mutate((d) => { d.education[i].school = v; })} />
                <Field label="Degree" value={entry.degree ?? ""}
                       placeholder="B.Tech"
                       onChange={(v) => mutate((d) => { d.education[i].degree = v; })} />
                <Field label="Field of study" value={entry.field_of_study ?? ""}
                       placeholder="Computer Engineering"
                       onChange={(v) => mutate((d) => {
                         d.education[i].field_of_study = v;
                       })} />
                <Field label="Grade" value={entry.grade ?? ""}
                       placeholder="First Class"
                       onChange={(v) => mutate((d) => { d.education[i].grade = v; })} />
              </div>
              <div className="mt-3">
                <DateRangeField
                  startDate={entry.start_date ?? ""}
                  endDate={entry.end_date ?? ""}
                  current={Boolean(entry.current)}
                  rawDates={entry.raw_dates}
                  currentLabel="I am still studying here"
                  onChange={(patch) => mutate((d) => {
                    Object.assign(d.education[i], patch);
                  })}
                />
              </div>
              <button
                type="button"
                onClick={() => mutate((d) => { d.education.splice(i, 1); })}
                className="mt-2 text-[11px] text-content-muted hover:text-reject-text"
              >
                Remove this entry
              </button>
            </div>
          ))}
          <Button
            variant="outline" size="sm"
            onClick={() => mutate((d) => {
              d.education = [...(d.education ?? []), {
                school: "", degree: "", field_of_study: "", grade: "",
                start_date: "", end_date: "", current: false, dates: "",
              }];
            })}
          >
            <Plus className="size-3.5" aria-hidden /> Add education
          </Button>
        </div>
      </section>
    </div>
  );
}
