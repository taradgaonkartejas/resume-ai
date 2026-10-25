import { useRef, useState } from "react";
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { restrictToVerticalAxis } from "@dnd-kit/modifiers";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { DateRangeField, Field, TextAreaField, TipsPanel } from "./fields";
import { dateLabel } from "@/lib/dates";
import { ExtrasEditor } from "./ExtrasEditor";
import { SortableBullets } from "./SortableBullets";
import { SortableChips } from "./SortableChips";
import { SortableList } from "./SortableList";
import { SortableRole } from "./SortableRole";
import { useStableIds } from "./useStableIds";
import type { StepKey, StructuredData } from "@/services";

/**
 * The editable surface for each step.
 *
 * The scored steps edit the scored sections: summary+education (10), skills
 * (30), experience (45) and projects (15). Contact and format are advisory
 * gates (knockout filter / parser blocker, 0 points). The last step,
 * "extras", edits the optional sections and is NOT scored — it renders only
 * ExtrasEditor, with no findings or recommendation cards, because the extras
 * category never emits any.
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
  if (step === "skills") return <SkillsSection {...props} />;
  if (step === "experience") return <ExperienceSection {...props} />;
  if (step === "projects") return <ProjectsSection {...props} />;
  if (step === "extras") {
    return <ExtrasEditor data={props.data} onChange={props.onChange} />;
  }
  return <FormatSection {...props} />;
}

/* ------------------------------------------------------------ projects --- */
/**
 * Projects step: SCORED at 15 points under the 2026-10-25 ATS table
 * (reversing the earlier unscored ruling — projects feed keyword context).
 * Drag concerns: whole entries via SortableRole and bullets inside each
 * entry via a nested DndContext, mirroring Experience.
 */
function ProjectsSection({ data, onChange, focusRef }: Props) {
  const entries = data.projects ?? [];

  const mutate = (fn: (d: StructuredData) => void) => {
    const next = clone(data);
    fn(next);
    onChange(next);
  };

  const { ids, reorder } = useStableIds(entries.length);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from < 0 || to < 0) return;
    reorder(from, to);
    mutate((d) => {
      const [moved] = d.projects.splice(from, 1);
      d.projects.splice(to, 0, moved);
    });
  };

  return (
    <div className="space-y-4" id="f-projects">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        modifiers={[restrictToVerticalAxis]}
        onDragEnd={onDragEnd}
      >
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          <div className="space-y-4">
            {entries.map((entry, i) => (
              <SortableRole key={ids[i]} id={ids[i]}>
                <Field
                  label="Project name"
                  value={entry.name ?? ""}
                  placeholder="Sidecar Tool"
                  onChange={(v) => mutate((d) => { d.projects[i].name = v; })}
                />
                <p className="mt-3 mb-1.5 text-xs font-medium text-content-muted">
                  Highlights
                </p>
                <SortableBullets
                  ownerKey={`prj_${i}`}
                  bullets={entry.bullets ?? []}
                  focusRef={focusRef}
                  onChange={(bullets) => mutate((d) => {
                    d.projects[i].bullets = bullets;
                  })}
                />
                <Button
                  variant="outline" size="sm" className="mt-2"
                  onClick={() => mutate((d) => {
                    d.projects[i].bullets = [
                      ...(d.projects[i].bullets ?? []), "",
                    ];
                  })}
                >
                  <Plus className="size-3.5" aria-hidden /> Add highlight
                </Button>
                <button
                  type="button"
                  className="mt-2 block text-xs text-content-muted underline underline-offset-2 hover:text-danger"
                  onClick={() => mutate((d) => { d.projects.splice(i, 1); })}
                >
                  Remove this project
                </button>
              </SortableRole>
            ))}
          </div>
        </SortableContext>
      </DndContext>

      <Button
        variant="outline"
        onClick={() => mutate((d) => {
          d.projects = [...(d.projects ?? []), { name: "", bullets: [""] }];
        })}
      >
        <Plus className="size-3.5" aria-hidden /> Add project
      </Button>
    </div>
  );
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
  // Education edits live here: the 2026-10-25 ATS table scores them under
  // the SAME 10 points as the summary ("Education & Summary"), so the step
  // owns both fields and the engine never splits their findings.
  return (
    <div className="space-y-5">
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
      <EducationEntries data={data} onChange={onChange} />
    </div>
  );
}

/* -------------------------------------------------------------- skills --- */
/**
 * Skills step: a 30-point scored category under the 2026-10-25 ATS table,
 * so it deserves its own editor rather than living inside the format step.
 * Chips inside groups are draggable (same ruling as experience bullets);
 * whole groups reorder via SortableList.
 */
/**
 * Inline type-ahead for appending one skill to a group. Controlled, on
 * purpose: the earlier version was an uncontrolled input that read and then
 * manually blanked the DOM node inside the Enter handler. Typed text lived
 * only in that DOM node, so any re-render around it could wipe an in-flight
 * edit — adding a second skill in the same group failed. Keeping the text in
 * React state removes the whole failure class, and the explicit refocus
 * means Enter → Enter → Enter appends a run of skills without re-clicking.
 */
function AddSkillInput({ onAdd }: { onAdd: (value: string) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");

  const commit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onAdd(trimmed);
    setValue("");
    requestAnimationFrame(() => ref.current?.focus());
  };

  return (
    <input
      ref={ref}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commit();
        }
      }}
      placeholder="Add skill…"
      aria-label="Add skill"
      className="min-w-24 flex-1 rounded-full bg-transparent px-2 py-0.5
                 text-xs outline-none placeholder:text-content-muted
                 focus-visible:bg-surface-3"
    />
  );
}

function SkillsSection({ data, onChange }: Props) {
  const mutate = (fn: (d: StructuredData) => void) => {
    const next = clone(data);
    fn(next);
    onChange(next);
  };

  const skills = data.skills ?? [];

  return (
    <div className="space-y-5">
      <section id="f-skills">
        <p className="mb-2 text-xs font-medium text-content-muted">
          Skills — grouped by category
        </p>
        <SortableList
          count={skills.length}
          idPrefix="skillgroup"
          onReorder={(from, to) => mutate((d) => {
            const [moved] = d.skills.splice(from, 1);
            d.skills.splice(to, 0, moved);
          })}
        >
          {(ids) => skills.map((group, i) => (
            <SortableRole key={ids[i]} id={ids[i]} label="Reorder this skill group">
              {/* pr-9: the card's drag grip is absolutely positioned at
                  right-2 top-2 (a 36px zone). Without the padding the
                  remove-group × ended up directly underneath it — the same
                  rule the experience cards follow. */}
              <div className="flex items-center gap-2 pr-9">
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
              <div id={`f-skills.${i}.items`}
                   className="mt-2 flex flex-wrap items-center gap-1.5">
                <SortableChips
                  ownerKey={`skill_${i}`}
                  items={group.items ?? []}
                  onChange={(items) => mutate((d) => {
                    d.skills[i].items = items;
                  })}
                  onRemove={(j) => mutate((d) => { d.skills[i].items.splice(j, 1); })}
                />
                <AddSkillInput
                  onAdd={(value) => mutate((d) => {
                    d.skills[i].items = [...(d.skills[i].items ?? []), value];
                  })}
                />
              </div>
            </SortableRole>
          ))}
        </SortableList>
        <div className="mt-2">
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
      <TipsPanel tips={[
        "Labelled groups parse into keyword buckets — one flat list does not.",
        "Eight or more real skills; every one you omit is a keyword the ATS cannot match you on.",
        "Repeating a skill earns nothing and costs the space another keyword could hold.",
      ]} />
    </div>
  );
}

/* ------------------------------------------------------------ education --
 * Extracted from the old Format step: the 2026-10-25 table folds education
 * into the summary's 10 points, so the Summary step renders these fields.
 * ----------------------------------------------------------------------- */
// Education findings have no target_ref flow into this editor yet, so it
// takes the data-only slice of Props rather than demanding a focusRef it
// would ignore.
function EducationEntries({ data, onChange }: Pick<Props, "data" | "onChange">) {
  const mutate = (fn: (d: StructuredData) => void) => {
    const next = clone(data);
    fn(next);
    onChange(next);
  };

  const education = data.education ?? [];

  return (
    <section id="f-education">
      <p className="mb-2 text-xs font-medium text-content-muted">Education</p>
      <SortableList
        count={education.length}
        idPrefix="education"
        onReorder={(from, to) => mutate((d) => {
          const [moved] = d.education.splice(from, 1);
          d.education.splice(to, 0, moved);
        })}
      >
        {(ids) => education.map((entry, i) => (
          <SortableRole key={ids[i]} id={ids[i]} label="Reorder this education entry">
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
          </SortableRole>
        ))}
      </SortableList>
      <div className="mt-2">
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
  );
}

/* ---------------------------------------------------------- experience --- */
/* Drag ordering is manual and immediate; the one-click "Reorder newest-first"
   on the ATS finding goes through POST /sections/experience/rerank instead,
   which refuses while suggestions are open. A hand drag deliberately does
   not: it is the user explicitly rearranging their own document, and the
   resulting ref drift is caught by SuggestionOut.is_stale. */
function ExperienceSection({ data, onChange, focusRef }: Props) {
  const entries = data.experience ?? [];

  const mutate = (fn: (d: StructuredData) => void) => {
    const next = clone(data);
    fn(next);
    onChange(next);
  };

  const { ids, reorder } = useStableIds(entries.length);
  const sensors = useSensors(
    // A small distance constraint so that clicking into a text field inside
    // the card is never mistaken for the start of a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from < 0 || to < 0) return;
    // Ids and data must receive the SAME permutation or every subsequent
    // drag addresses the wrong card.
    reorder(from, to);
    mutate((d) => {
      const [moved] = d.experience.splice(from, 1);
      d.experience.splice(to, 0, moved);
    });
  };

  return (
    <div className="space-y-4">
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        modifiers={[restrictToVerticalAxis]}
        onDragEnd={onDragEnd}
      >
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          <div className="space-y-4">
      {entries.map((entry, i) => (
        <SortableRole key={ids[i]} id={ids[i]}>
          <div className="grid gap-3 pr-9 sm:grid-cols-2">
            <Field label="Company" value={entry.company ?? ""}
                   onChange={(v) => mutate((d) => { d.experience[i].company = v; })} />
            <Field label="Role" value={entry.role ?? ""}
                   onChange={(v) => mutate((d) => { d.experience[i].role = v; })} />
            <Field label="Location" value={entry.location ?? ""}
                   placeholder="Pune, India"
                   onChange={(v) => mutate((d) => { d.experience[i].location = v; })} />
            <div className="sm:col-span-2">
              <Field label="Company descriptor"
                     value={entry.descriptor ?? ""}
                     placeholder="Bootstrapped EdTech startup, 50 staff"
                     onChange={(v) => mutate((d) => {
                       d.experience[i].descriptor = v;
                     })} />
              <p className="mt-1 text-[11px] text-content-muted">
                One italic line under the employer, for companies a reader will
                not recognise. Leave empty to show nothing.
              </p>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <Switch
              id={`f-exp_${i}-condensed`}
              checked={Boolean(entry.condensed)}
              onCheckedChange={(checked: boolean) =>
                mutate((d) => {
                  d.experience[i].condensed = checked || undefined;
                })
              }
            />
            <Label
              htmlFor={`f-exp_${i}-condensed`}
              className="text-xs text-content-muted"
            >
              Condense to one line (older role) — hides the achievements
            </Label>
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
            <SortableBullets
              ownerKey={`exp_${i}`}
              bullets={entry.bullets ?? []}
              focusRef={focusRef}
              onChange={(bullets) => mutate((d) => {
                d.experience[i].bullets = bullets;
              })}
            />
            <Button
              variant="outline" size="sm"
              onClick={() => mutate((d) => {
                d.experience[i].bullets = [...(d.experience[i].bullets ?? []), ""];
              })}
            >
              <Plus className="size-3.5" aria-hidden /> Add achievement
            </Button>
          </div>
        </SortableRole>
      ))}
          </div>
        </SortableContext>
      </DndContext>

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

/* -------------------------------------------------------------- format ---
 * Advisory-only since the 2026-10-25 ruling: the format pass is a pass/fail
 * parser blocker (thin/thick resume, overlong bullets, dates a parser cannot
 * read, decorative glyphs, newest-first ordering). Its findings name what to
 * change elsewhere; this step hosts no fields of its own.
 * ----------------------------------------------------------------------- */
function FormatSection({ data }: Props) {
  const words = resumeWordCount(data);
  return (
    <div className="space-y-3">
      <p className="text-xs text-content-muted">
        These checks gate whether an ATS can parse the file at all — pass/fail,
        not points. Fix them where the content lives: length and bullets in
        Work Experience, dates in whichever entry flags them.
      </p>
      <p className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-content-muted">
        Current length: <span className="font-medium text-content">{words} words</span>
        <span className="mx-1.5 text-content-faint" aria-hidden>·</span>
        target 250–900
      </p>
      <TipsPanel tips={[
        "Keep bullets under about 30 words so they get read.",
        "Use dates like “Jan 2024 – Mar 2026”; one odd entry can corrupt the parsed timeline.",
        "Roles newest-first — parsers take the first entry as your current employer.",
        "Plain text and standard bullets only. Arrows, stars and emoji tokenise as unknown characters.",
      ]} />
    </div>
  );
}

function resumeWordCount(data: StructuredData): number {
  const parts: string[] = [data.summary?.text ?? ""];
  for (const e of data.experience ?? []) parts.push(...(e.bullets ?? []));
  for (const p of data.projects ?? []) parts.push(p.name ?? "", ...(p.bullets ?? []));
  for (const g of data.skills ?? []) parts.push(g.label ?? "", ...(g.items ?? []));
  for (const e of data.education ?? []) {
    parts.push(e.school ?? "", e.degree ?? "", e.field_of_study ?? "", e.grade ?? "");
  }
  return parts.join(" ").trim().split(/\s+/).filter(Boolean).length;
}
