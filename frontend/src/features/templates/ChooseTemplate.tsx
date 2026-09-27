import { useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/app/ThemeToggle";
import { TemplateGrid } from "@/features/templates/TemplateGrid";
import { useApplyTemplate, useResume, useTemplates } from "@/api/queries";
import { useNav, useResumeIdParam } from "@/app/nav";
import { emptyResume } from "@/services";

/**
 * Step 2 of the upload flow: choose a template.
 *
 * A forced full-screen step rather than a modal, because after extraction the
 * user has never seen their resume rendered. This is the first moment the
 * parse becomes visible, so it doubles as a "did we read your file
 * correctly?" check before any analysis or tailoring happens.
 */
export function ChooseTemplate() {
  const resumeId = useResumeIdParam() ?? "";
  const nav = useNav();
  const { data: resume, isLoading: resumeLoading } = useResume(resumeId);
  const { data: templates, isLoading, isError, error } = useTemplates();
  const apply = useApplyTemplate(resumeId);

  const [selected, setSelected] = useState(resume?.template_key ?? "modern");
  // Adjust during render when the resume arrives, rather than in an effect:
  // react-hooks/set-state-in-effect forbids the effect form, and this avoids
  // a flash of the wrong selection.
  const [syncedFor, setSyncedFor] = useState<string | null>(null);
  if (resume && syncedFor !== resume.id) {
    setSyncedFor(resume.id);
    setSelected(resume.template_key);
  }

  const data = resume?.structured_data ?? emptyResume();

  // Catching a bad parse here is much cheaper than after the user has
  // tailored it. Deliberately quiet: a warning, not a blocker.
  const thin =
    Boolean(resume) &&
    (data.experience.length === 0 || !data.contact.email || !data.contact.name);

  function finish() {
    if (!resume) return;
    // `replace`: the wizard is one-way, so Back from the workspace should
    // return to the library, not to a template choice already made.
    if (selected === resume.template_key) {
      nav.workspace(resumeId, undefined, { replace: true });
      return;
    }
    apply.mutate(selected, {
      onSuccess: () => nav.workspace(resumeId, undefined, { replace: true }),
    });
  }

  return (
    <div className="flex h-dvh flex-col bg-canvas text-content">
      <header className="shrink-0 border-b border-line-soft bg-surface-1 px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs text-content-muted">
              <span className="inline-flex items-center gap-1 text-brand-text">
                <Check className="size-3.5" aria-hidden /> Resume imported
              </span>
              <span aria-hidden>›</span>
              <span className="font-medium text-content">Choose a template</span>
            </div>
            <h1 className="mt-1 truncate text-xl font-bold">
              Choose a template for “{resume?.title ?? "your resume"}”
            </h1>
            <p className="mt-0.5 text-sm text-content-muted">
              Previews show your real content. You can change this at any time.
            </p>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <ThemeToggle />
            <Button variant="outline" onClick={nav.library}>
              <ArrowLeft className="size-4" aria-hidden />
              My Resumes
            </Button>
            <Button onClick={finish} disabled={apply.isPending || !resume}>
              {apply.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Continue
              <ArrowRight className="size-4" aria-hidden />
            </Button>
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-7xl">
          {thin && (
            <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 px-3 py-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
              <p className="text-sm text-content">
                Some details look missing from the extracted resume
                {data.experience.length === 0 && " (no work experience found)"}.
                Pick a template and fix the content in the editor.
              </p>
            </div>
          )}

          {apply.isError && (
            <p role="alert" className="mb-3 text-sm text-reject-text">
              {(apply.error as Error)?.message ?? "Could not apply the template."}
            </p>
          )}

          {isLoading || resumeLoading ? (
            <div className="grid h-64 place-items-center">
              <Loader2 className="size-6 animate-spin text-brand-text" aria-hidden />
            </div>
          ) : isError ? (
            <div className="grid h-64 place-items-center text-center">
              <p className="text-sm text-reject-text">
                Could not load templates. {(error as Error)?.message}
              </p>
            </div>
          ) : (
            <TemplateGrid
              templates={templates ?? []}
              data={data}
              selected={selected}
              onSelect={setSelected}
              columns={4}
            />
          )}
        </div>
      </main>
    </div>
  );
}
