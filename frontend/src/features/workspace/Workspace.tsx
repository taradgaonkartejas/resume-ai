import { useEffect, useState } from "react";
import {
  ArrowLeft,
  Check,
  Eye,
  FileUp,
  LayoutTemplate,
  Loader2,
  Minus,
  Plus,
  Redo2,
  Undo2,
  X,
} from "lucide-react";
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
import {
  useAcceptAll,
  useParseStatus,
  useRedo,
  useResume,
  useResumes,
  useRestoreVersion,
  useTemplates,
  useUndo,
} from "@/api/queries";
import { emptyResume } from "@/services";
import { cn } from "cn";

/**
 * The resume editor.
 *
 * Reached from My Resumes or at the end of the upload wizard.
 *
 * Left: Resume Analysis / Tailor Resume. Right: the live preview.
 * The centre chat pane is the remaining increment.
 */
export function Workspace() {
  const { activeUserId, modal, zoom, changedRefs, showChanges, baselineVersion } =
    useAppState();
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
  const restore = useRestoreVersion(resumeId);
  const undo = useUndo(resumeId);
  const redo = useRedo(resumeId);
  // `null` session = every pending suggestion on the document, which is what
  // a toolbar sitting above the whole preview should mean. The per-session
  // Accept all in TailorPane stays scoped to its own session.
  const acceptAll = useAcceptAll(resumeId, null);
  // Only polls while a parse is in flight; once ready it is a single read.
  const { data: parse } = useParseStatus(resumeId, Boolean(resumeId));
  const advisories = parse?.parse_advisories ?? [];
  const { data: resume, isLoading: resumeLoading } = useResume(resumeId);

  // Refs are positional -- exp_0.bullet_0 means "whatever is at that index".
  // Carrying them to another resume would highlight an unrelated line, so
  // the set is dropped whenever the document changes.
  useEffect(() => {
    dispatch({ type: "clearChanges" });
  }, [resumeId, dispatch]);

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

        {/* A SUCCESSFUL parse that may still have dropped something.
            Separate from "parse failed", which means we got nothing at
            all. Advisory only -- it never moves the score, because the
            problem is in the file the user uploaded rather than in the
            resume's content. Rendered only when there is something to
            say; a banner on every upload is a banner nobody reads. */}
        {resume?.parse_status === "ready" && advisories.length > 0 && (
          <details className="group">
            <summary
              className="cursor-pointer list-none rounded-full border border-warning/60
                         px-2 py-0.5 text-[11px] font-medium text-warning
                         hover:bg-warning/10"
              title="How machine-readable the file you uploaded was"
            >
              {advisories.length} readability note
              {advisories.length === 1 ? "" : "s"}
            </summary>
            <ul
              className="absolute z-20 mt-1 max-w-md space-y-1.5 rounded-lg border
                         border-line-soft bg-surface-2 p-2.5 text-[11px] leading-relaxed
                         text-content-muted shadow-lg"
            >
              {advisories.map((line) => (
                <li key={line} className="flex gap-1.5">
                  <span className="text-warning" aria-hidden>
                    •
                  </span>
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </details>
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

          The left column is ~560px rather than 340px because it hosts the
          revision card — original text, the revised text, a REASONING block
          and three buttons — and, on the experience step, the achievement
          textareas. Long bullets are the unit of work here: at 460px a
          six-line achievement wrapped into a scroll box barely taller than
          the buttons beside it, which is the wrong thing to make small.
          It also takes a larger share of the free space (1.2fr), so the
          extra room on a wide screen goes to the pane being worked in.

          Consequence, accepted deliberately: the three-column layout starts at
          2xl (1536px) instead of xl (1280px). Between those widths you get a
          wide Analysis pane plus chat, and the live preview hides — it is
          already hidden below 1280px, so this moves an existing boundary
          rather than introducing one. */}
      <main
        className="grid min-h-0 flex-1 grid-cols-[minmax(560px,1.2fr)_minmax(0,1fr)]
                   2xl:grid-cols-[minmax(560px,1.15fr)_minmax(340px,0.7fr)_minmax(0,1.1fr)]"
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
            {/* ---- zoom + history, right of the title ---- */}
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

              <span className="mx-1 h-4 w-px bg-line-soft" />

              {/* Undo/redo move the SAME cursor Decline Changes moves. One
                  history, three controls -- see useVersionStep. */}
              <button
                aria-label="Undo"
                title="Undo the last change"
                disabled={!hasResume || undo.isPending}
                onClick={() => undo.mutate()}
                className="grid size-7 place-items-center rounded-md border border-line-soft text-content-muted hover:text-content disabled:opacity-40"
              >
                <Undo2 className="size-3.5" />
              </button>
              <button
                aria-label="Redo"
                title="Redo the change you just undid"
                disabled={!hasResume || redo.isPending}
                onClick={() => redo.mutate()}
                className="grid size-7 place-items-center rounded-md border border-line-soft text-content-muted hover:text-content disabled:opacity-40"
              >
                <Redo2 className="size-3.5" />
              </button>
            </div>
          </div>

          {/* ---- Accept / Show / Decline ----

              All three stay visible and go disabled rather than vanishing.
              A control that disappears teaches nothing; a greyed one tells
              you the action exists and why it is unavailable right now. */}
          <div className="flex shrink-0 items-center justify-center gap-2 border-b border-line-soft bg-surface-1 px-4 pb-2">
            <button
              onClick={() => acceptAll.mutate()}
              disabled={!hasResume || acceptAll.isPending}
              title="Apply every pending suggestion on this resume"
              className="flex items-center gap-1.5 rounded-full bg-accept px-4 py-1.5
                         text-xs font-medium text-on-accept transition-opacity
                         hover:opacity-90 disabled:opacity-40"
            >
              {acceptAll.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Check className="size-3.5" />
              )}
              Accept Changes
            </button>

            <button
              onClick={() => dispatch({ type: "setShowChanges", on: !showChanges })}
              aria-pressed={showChanges}
              disabled={changedRefs.length === 0}
              title="Highlight text changed by accepted suggestions in this session"
              className={cn(
                "flex items-center gap-1.5 rounded-full px-4 py-1.5 text-xs",
                "font-medium transition-colors disabled:opacity-40",
                showChanges
                  ? "bg-brand text-on-brand hover:bg-brand-hover"
                  : "border border-line-soft text-content-muted hover:border-brand-text hover:text-content",
              )}
            >
              <Eye className="size-3.5" />
              {showChanges ? "Hide Changes" : "Show Changes"}
              {changedRefs.length > 0 && (
                <span className="tabular-nums opacity-70">{changedRefs.length}</span>
              )}
            </button>

            {/* Decline = revert, and revert is undo. This restores the cursor
                to where it stood before the first of the changes currently
                highlighted, so it undoes exactly what Show Changes points
                at. Disabled without a baseline: reverting to a guess is
                worse than offering nothing. */}
            <button
              onClick={() => {
                if (
                  baselineVersion !== null &&
                  window.confirm(
                    `Undo ${changedRefs.length} accepted change` +
                      `${changedRefs.length === 1 ? "" : "s"} and restore the ` +
                      "resume to how it was before? You can redo this.",
                  )
                ) {
                  restore.mutate(baselineVersion);
                }
              }}
              disabled={
                changedRefs.length === 0 || baselineVersion === null || restore.isPending
              }
              title="Revert every change accepted in this session"
              className="flex items-center gap-1.5 rounded-full bg-reject px-4 py-1.5
                         text-xs font-medium text-on-reject transition-opacity
                         hover:opacity-90 disabled:opacity-40"
            >
              {restore.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <X className="size-3.5" />
              )}
              Decline Changes
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-auto bg-canvas p-6">
            {resumeLoading ? (
              <div className="grid h-full place-items-center">
                <Loader2 className="size-6 animate-spin text-brand-text" aria-hidden />
              </div>
            ) : (
              <ResumePreview
                data={data}
                tokens={activeTokens}
                zoom={zoom}
                changedRefs={changedRefs}
                showChanges={showChanges}
              />
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
        onStarted={(workingId) => {
          // Normally the same resume we are already on, so this just opens
          // the Tailor tab. It is still driven by the returned id so that
          // `fork: true` would navigate to the child instead of stranding
          // the user on the untouched base.
          nav.workspace(workingId, "tailor");
        }}
      />
    </div>
  );
}
