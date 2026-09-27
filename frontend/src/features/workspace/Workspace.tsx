import { useState } from "react";
import { ArrowLeft, FileUp, LayoutTemplate, Loader2, Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { UserSwitcher } from "@/app/UserSwitcher";
import { ThemeToggle } from "@/app/ThemeToggle";
import { TemplateModal } from "@/features/templates/TemplateModal";
import { ResumePreview } from "@/features/preview/ResumePreview";
import { AnalysisPane } from "@/features/analysis/AnalysisPane";
import { ChatPane } from "@/features/chat/ChatPane";
import { TailorPane } from "@/features/tailor/TailorPane";
import { TailorModal } from "@/features/tailor/TailorModal";
import { useSearchParams } from "react-router-dom";
import { useAppDispatch, useAppState, type LeftTab } from "@/lib/AppState";
import { useNav, useResumeIdParam } from "@/app/nav";
import { useResume, useResumes, useTemplates } from "@/api/queries";
import { emptyResume } from "@/services";

/**
 * The resume editor.
 *
 * Reached from My Resumes or at the end of the upload wizard.
 *
 * Left: Resume Analysis / Tailor Resume. Right: the live preview.
 * The centre chat pane is the remaining increment.
 */
export function Workspace() {
  const { activeUserId, modal, zoom } = useAppState();
  const dispatch = useAppDispatch();
  const nav = useNav();
  const routeResumeId = useResumeIdParam();

  // The left tab lives in the URL so "Continue Tailoring" is linkable and a
  // refresh keeps your place. Unknown values fall back to analysis rather
  // than rendering nothing.
  const [searchParams, setSearchParams] = useSearchParams();
  const leftTab: LeftTab = searchParams.get("tab") === "tailor" ? "tailor" : "analysis";
  const setLeftTab = (tab: LeftTab) =>
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("tab", tab);
      // Tab switches are not history entries; Back should leave the
      // workspace, not step through tabs the user clicked.
      return next;
    }, { replace: true });

  const { data: resumes } = useResumes(activeUserId);
  // Fall back to the user's most recent resume so a refresh does not dump you
  // back on the empty state.
  const resumeId = routeResumeId ?? resumes?.[0]?.id ?? null;
  const { data: resume, isLoading: resumeLoading } = useResume(resumeId);

  const { data: templates } = useTemplates();
  const [tailorOpen, setTailorOpen] = useState(false);

  const data = resume?.structured_data ?? emptyResume();
  const templateKey = resume?.template_key ?? "modern";
  const hasResume = Boolean(resume);

  // Styling comes from the backend, not a local map. Undefined while the
  // template list loads; ResumePreview falls back to the shared defaults.
  const activeTokens = templates?.find((t) => t.key === templateKey)?.design_tokens;
  const activeTemplateName =
    templates?.find((t) => t.key === templateKey)?.name ?? templateKey;

  return (
    <div className="flex h-dvh flex-col bg-canvas text-content">
      {/* ---------------- top bar ---------------- */}
      <header className="flex shrink-0 items-center gap-3 border-b border-line-soft bg-surface-1 px-4 py-2.5">
        <button
          onClick={nav.library}
          className="flex items-center gap-1.5 rounded-md px-1.5 py-1 text-sm text-content-muted hover:text-content"
        >
          <ArrowLeft className="size-4" aria-hidden />
          My Resumes
        </button>

        <span className="min-w-0 truncate text-sm font-medium text-content">
          {resume?.title ?? "No resume selected"}
        </span>

        {resume?.parse_status === "failed" && (
          <span className="rounded-full border border-warning px-2 py-0.5 text-[11px] font-medium text-warning">
            parse failed
          </span>
        )}

        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => dispatch({ type: "openModal", modal: "templates" })}
            disabled={!hasResume}
          >
            <LayoutTemplate className="size-4" aria-hidden />
            Templates
          </Button>
          <Button onClick={() => dispatch({ type: "openModal", modal: "import" })}>
            <FileUp className="size-4" aria-hidden />
            Import resume
          </Button>
          <ThemeToggle />
          <UserSwitcher />
        </div>
      </header>

      {/* ---------------- body ---------------- */}
      {/* Chat is a fixed, narrower column and the PREVIEW takes the slack:
          chat lines past ~70 characters are hard to scan, and the resume is
          the thing that benefits from extra width.

          The left column is ~460px rather than 340px because it now hosts the
          revision card — original text, the revised text, a REASONING block
          and three buttons. That does not fit in 340px without wrapping the
          buttons onto their own line.

          Consequence, accepted deliberately: the three-column layout starts at
          2xl (1536px) instead of xl (1280px). Between those widths you get a
          wide Analysis pane plus chat, and the live preview hides — it is
          already hidden below 1280px, so this moves an existing boundary
          rather than introducing one. */}
      <main
        className="grid min-h-0 flex-1 grid-cols-[minmax(440px,1fr)_1fr]
                   2xl:grid-cols-[minmax(460px,0.95fr)_minmax(380px,0.85fr)_minmax(0,1.2fr)]"
      >
        {/* left: the two working modes */}
        <aside className="flex min-h-0 flex-col border-r border-line-soft bg-surface-1">
          <div className="flex shrink-0 items-center gap-1 border-b border-line-soft px-3 pt-2">
            {(["analysis", "tailor"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setLeftTab(t)}
                className={
                  "-mb-px border-b-2 px-3 py-2 text-sm transition-colors " +
                  (leftTab === t
                    ? "border-brand font-semibold text-content"
                    : "border-transparent text-content-muted hover:text-content")
                }
              >
                {t === "analysis" ? "Resume Analysis" : "Tailor Resume"}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {!hasResume ? (
              <p className="px-1 py-8 text-center text-xs text-content-muted">
                Select a resume to analyse or tailor it.
              </p>
            ) : leftTab === "analysis" ? (
              <AnalysisPane resumeId={resumeId} />
            ) : (
              <TailorPane
                resumeId={resumeId}
                onStartTailoring={() => setTailorOpen(true)}
              />
            )}
          </div>
        </aside>

        {/* centre: AI chat */}
        <ChatPane resumeId={resumeId} />

        {/* right: live preview. Hidden below 2xl, and that breakpoint MUST
            match the grid's third column above — showing this section while
            the grid still has two columns would stack it into the chat
            column instead of beside it. */}
        <section className="hidden min-h-0 flex-col 2xl:flex">
          <div className="flex shrink-0 items-center gap-2 border-b border-line-soft bg-surface-1 px-4 py-2">
            <span className="text-xs text-content-muted">Live preview</span>
            <button
              onClick={() => dispatch({ type: "openModal", modal: "templates" })}
              disabled={!hasResume}
              className="rounded-full border border-line-soft px-2 py-0.5 text-[11px] text-content-muted hover:border-brand-text hover:text-content disabled:opacity-50"
            >
              {activeTemplateName}
            </button>
            <div className="ml-auto flex items-center gap-1">
              <button
                aria-label="Zoom out"
                onClick={() => dispatch({ type: "setZoom", zoom: zoom - 10 })}
                className="grid size-7 place-items-center rounded-md border border-line-soft text-content-muted hover:text-content"
              >
                <Minus className="size-3.5" />
              </button>
              <span className="w-12 text-center text-xs tabular-nums text-content-muted">
                {zoom}%
              </span>
              <button
                aria-label="Zoom in"
                onClick={() => dispatch({ type: "setZoom", zoom: zoom + 10 })}
                className="grid size-7 place-items-center rounded-md border border-line-soft text-content-muted hover:text-content"
              >
                <Plus className="size-3.5" />
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-auto bg-canvas p-6">
            {resumeLoading ? (
              <div className="grid h-full place-items-center">
                <Loader2 className="size-6 animate-spin text-brand-text" aria-hidden />
              </div>
            ) : (
              <ResumePreview data={data} tokens={activeTokens} zoom={zoom} />
            )}
          </div>
        </section>
      </main>

      {/* The import modal is hoisted to App: it is reachable from both the
          library and here, and its success moves the user to the template
          step. */}
      <TemplateModal
        open={modal === "templates"}
        onOpenChange={(open) =>
          dispatch(
            open ? { type: "openModal", modal: "templates" } : { type: "closeModal" },
          )
        }
        resumeId={resumeId}
        data={data}
        currentKey={templateKey}
      />

      <TailorModal
        open={tailorOpen}
        onOpenChange={setTailorOpen}
        resumeId={resumeId}
        resumeTitle={resume?.title ?? "this resume"}
        onStarted={(childId) => {
          // Tailoring forked: move to the CHILD, otherwise the user keeps
          // editing the untouched base and wonders why nothing changed.
          nav.workspace(childId, "tailor");
        }}
      />
    </div>
  );
}
