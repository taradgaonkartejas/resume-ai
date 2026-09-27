import { useState } from "react";
import { ChevronDown, ChevronUp, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Field } from "./fields";
import type { ExtraEntry, ExtraKind, ExtraSection, StructuredData } from "@/services";

/**
 * Optional sections: certifications, languages, awards, publications,
 * references and anything the user names.
 *
 * One generic four-field entry serves all six kinds; the PRESET supplies the
 * labels, and a field whose label is "" is not part of that kind and is never
 * rendered. That is what keeps a Languages entry down to two inputs instead of
 * four, while preview, three exporters and the parser each carry one code path
 * rather than six.
 *
 * Nothing here affects the ATS score, by design — scoring "do you hold
 * certifications" would mark a novelist down for not being a sysadmin, which
 * critiques a career rather than a document.
 */

type Labels = Record<keyof ExtraEntry, string>;

/** Mirrors EXTRA_PRESETS in backend/app/services/resume_ops.py. */
const PRESETS: Record<ExtraKind, { title: string; labels: Labels }> = {
  certifications: {
    title: "Certifications",
    labels: { primary: "Certification", secondary: "Issuer", date: "Year", detail: "" },
  },
  languages: {
    title: "Languages",
    labels: { primary: "Language", secondary: "Fluency", date: "", detail: "" },
  },
  awards: {
    title: "Awards & Honors",
    labels: { primary: "Award", secondary: "Awarded by", date: "Year", detail: "" },
  },
  publications: {
    title: "Publications",
    labels: { primary: "Title", secondary: "Venue / journal", date: "Year", detail: "Link" },
  },
  references: {
    title: "References",
    labels: { primary: "Name", secondary: "Title & company", date: "", detail: "Email / phone" },
  },
  custom: {
    title: "Additional Information",
    labels: { primary: "Item", secondary: "Detail", date: "", detail: "" },
  },
};

const ORDER: ExtraKind[] = [
  "certifications", "languages", "awards", "publications", "references", "custom",
];

const FIELDS = ["primary", "secondary", "date", "detail"] as const;

const emptyEntry = (): ExtraEntry => ({
  primary: "", secondary: "", date: "", detail: "",
});

const clone = (d: StructuredData): StructuredData =>
  JSON.parse(JSON.stringify(d)) as StructuredData;

type Props = {
  data: StructuredData;
  onChange: (next: StructuredData) => void;
};

export function ExtrasEditor({ data, onChange }: Props) {
  const [adding, setAdding] = useState(false);
  // Tolerate a document stored before `extras` existed: migrate() backfills it
  // server-side, but the editor may render optimistically before that lands.
  const extras: ExtraSection[] = data.extras ?? [];

  const write = (next: ExtraSection[]) => {
    const copy = clone(data);
    copy.extras = next;
    onChange(copy);
  };

  const addSection = (kind: ExtraKind) => {
    setAdding(false);
    write([...extras, { kind, title: PRESETS[kind].title, entries: [emptyEntry()] }]);
  };

  const patchSection = (i: number, patch: Partial<ExtraSection>) =>
    write(extras.map((s, k) => (k === i ? { ...s, ...patch } : s)));

  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= extras.length) return;
    const next = [...extras];
    [next[i], next[j]] = [next[j], next[i]];
    write(next);
  };

  // A preset may appear once; "custom" as often as the user likes.
  const used = new Set(extras.map((s) => s.kind));

  return (
    <div className="grid gap-5">
      <p className="text-xs leading-relaxed text-content-muted">
        Certifications, languages, awards and anything else worth listing. These
        sections appear on your resume and in every export, but they do not
        change your ATS score.
      </p>

      {extras.length === 0 && !adding && (
        <p className="rounded-lg border border-dashed border-line/60 px-4 py-6 text-center text-sm text-content-muted">
          No optional sections yet.
        </p>
      )}

      {extras.map((section, i) => {
        const labels = PRESETS[section.kind]?.labels ?? PRESETS.custom.labels;
        const shown = FIELDS.filter((f) => labels[f]);
        return (
          <section
            key={i}
            className="grid gap-3 rounded-lg border border-line/60 bg-surface-2 p-4"
          >
            <header className="flex items-end gap-2">
              <div className="grid flex-1 gap-1.5">
                <Label
                  htmlFor={`extra-title-${i}`}
                  className="text-xs text-content-muted"
                >
                  Section heading
                </Label>
                <Input
                  id={`extra-title-${i}`}
                  value={section.title}
                  onChange={(e) => patchSection(i, { title: e.target.value })}
                  placeholder={PRESETS[section.kind]?.title}
                />
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Move ${section.title} up`}
                disabled={i === 0}
                onClick={() => move(i, -1)}
              >
                <ChevronUp className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Move ${section.title} down`}
                disabled={i === extras.length - 1}
                onClick={() => move(i, 1)}
              >
                <ChevronDown className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove ${section.title}`}
                onClick={() => write(extras.filter((_, k) => k !== i))}
              >
                <Trash2 className="size-4" />
              </Button>
            </header>

            {section.entries.map((entry, j) => (
              <div
                key={j}
                className="grid gap-2 rounded-md border border-line/40 p-3"
                style={{
                  gridTemplateColumns: `repeat(${Math.min(shown.length, 2)}, minmax(0, 1fr))`,
                }}
              >
                {shown.map((f) => (
                  <Field
                    key={f}
                    label={labels[f]}
                    value={entry[f] ?? ""}
                    onChange={(v) =>
                      patchSection(i, {
                        entries: section.entries.map((e, k) =>
                          k === j ? { ...e, [f]: v } : e,
                        ),
                      })
                    }
                  />
                ))}
                <div
                  className="flex justify-end"
                  style={{ gridColumn: `span ${Math.min(shown.length, 2)}` }}
                >
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      patchSection(i, {
                        entries: section.entries.filter((_, k) => k !== j),
                      })
                    }
                  >
                    <Trash2 className="mr-1.5 size-3.5" />
                    Remove entry
                  </Button>
                </div>
              </div>
            ))}

            <Button
              variant="outline"
              size="sm"
              className="justify-self-start"
              onClick={() =>
                patchSection(i, { entries: [...section.entries, emptyEntry()] })
              }
            >
              <Plus className="mr-1.5 size-3.5" />
              Add entry
            </Button>
          </section>
        );
      })}

      {adding ? (
        <div className="grid gap-2 rounded-lg border border-line/60 p-4">
          <p className="text-xs text-content-muted">Choose a section to add</p>
          <div className="flex flex-wrap gap-2">
            {ORDER.map((kind) => (
              <Button
                key={kind}
                variant="outline"
                size="sm"
                disabled={kind !== "custom" && used.has(kind)}
                onClick={() => addSection(kind)}
              >
                {kind === "custom" ? "Custom\u2026" : PRESETS[kind].title}
              </Button>
            ))}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="justify-self-start"
            onClick={() => setAdding(false)}
          >
            Cancel
          </Button>
        </div>
      ) : (
        <Button
          variant="outline"
          size="sm"
          className="justify-self-start"
          onClick={() => setAdding(true)}
        >
          <Plus className="mr-1.5 size-4" />
          Add section
        </Button>
      )}
    </div>
  );
}
