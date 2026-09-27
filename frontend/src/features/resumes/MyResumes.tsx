import { useState } from "react";
import {
  Copy,
  FileText,
  FilePlus2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Search,
  Target,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { UserSwitcher } from "@/app/UserSwitcher";
import { ThemeToggle } from "@/app/ThemeToggle";
import { ResumePreview } from "@/features/preview/ResumePreview";
import { ScoreRing } from "@/features/analysis/ScoreRing";
import { TailorModal } from "@/features/tailor/TailorModal";
import { band } from "@/lib/score";
import {
  useCreateResume,
  useDeleteResume,
  useForkResume,
  useRenameResume,
  useResumes,
  useTemplates,
} from "@/api/queries";
import { useAppDispatch, useAppState } from "@/lib/AppState";
import { useNav } from "@/app/nav";
import type { ResumeSummaryOut } from "@/services";
import { cn } from "cn";

type Tab = "all" | "base" | "tailored";

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * My Resumes — the app's home screen.
 *
 * Every resume, base or tailored, is a card. Tailoring FORKS a new row
 * server-side, which is what lets one base resume serve many applications
 * while staying pristine.
 */
export function MyResumes() {
  const { activeUserId } = useAppState();
  const dispatch = useAppDispatch();
  const nav = useNav();
  const { data: resumes, isLoading } = useResumes(activeUserId);
  const { data: templates } = useTemplates();

  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [tailorFor, setTailorFor] = useState<ResumeSummaryOut | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  const create = useCreateResume({
    onSuccess: (resume) => nav.templateStep(resume.id),
  });
  const fork = useForkResume();
  const rename = useRenameResume();
  const remove = useDeleteResume();

  const all = resumes ?? [];
  const counts = {
    all: all.length,
    base: all.filter((r) => r.kind === "base").length,
    tailored: all.filter((r) => r.kind === "tailored").length,
  };

  const needle = query.trim().toLowerCase();
  const visible = all
    .filter((r) => (tab === "all" ? true : r.kind === tab))
    .filter(
      (r) =>
        !needle ||
        r.title.toLowerCase().includes(needle) ||
        r.tailored_for.toLowerCase().includes(needle),
    );

  function openResume(r: ResumeSummaryOut) {
    nav.workspace(r.id);
  }

  /**
   * One button, two jobs.
   *
   * A TAILORED resume already has a session, so "Continue Tailoring" goes
   * straight to its suggestions. A BASE resume has no job description yet,
   * so it must ask for one first — and that fork is what produces the new
   * card.
   */
  function startTailoring(r: ResumeSummaryOut) {
    if (r.kind === "tailored") {
      nav.workspace(r.id, "tailor");
      return;
    }
    setTailorFor(r);
  }

  /** The pencil renames in place; the card body opens the editor. */
  function openAnalysis(r: ResumeSummaryOut) {
    nav.workspace(r.id, "analysis");
  }

  function submitRename(id: string) {
    const title = draftTitle.trim();
    setRenaming(null);
    if (title) rename.mutate({ resumeId: id, title });
  }

  return (
    <div className="flex h-dvh flex-col bg-canvas text-content">
      <header className="flex shrink-0 items-center gap-3 border-b border-line-soft bg-surface-1 px-6 py-3">
        <span className="rounded-md bg-brand-gradient px-2 py-1 text-xs font-bold text-white">
          ResumeAI
        </span>
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          <UserSwitcher />
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-wrap items-start gap-4">
            <div className="min-w-0">
              <h1 className="text-3xl font-bold tracking-tight">My Resumes</h1>
              <p className="mt-1 max-w-xl text-sm text-content-muted">
                Import or create a resume, then analyse it or tailor it to any job —
                both tools live inside the resume editor.
              </p>
            </div>
            <Button
              className="ml-auto"
              onClick={() => dispatch({ type: "openModal", modal: "import" })}
            >
              <FilePlus2 className="size-4" aria-hidden />
              New Resume
            </Button>
          </div>

          {/* tabs + search */}
          <div className="mt-6 flex flex-wrap items-center gap-4 border-b border-line-soft">
            <div className="flex items-center gap-1">
              {(["all", "base", "tailored"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={cn(
                    "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm capitalize transition-colors",
                    tab === t
                      ? "border-brand font-semibold text-content"
                      : "border-transparent text-content-muted hover:text-content",
                  )}
                >
                  {t === "all" ? "All Resume" : `${t} Resume`}
                  <span className="rounded bg-surface-3 px-1.5 text-[11px] text-content-muted">
                    {counts[t]}
                  </span>
                </button>
              ))}
            </div>

            <div className="relative ml-auto mb-2 w-full max-w-sm">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-content-muted"
                aria-hidden
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search resumes by title or role…"
                aria-label="Search resumes"
                className="w-full rounded-lg border border-line-soft bg-surface-1 py-2 pl-9 pr-3 text-sm text-content outline-none placeholder:text-content-muted focus:border-brand-text"
              />
            </div>
          </div>

          {/* grid */}
          {isLoading ? (
            <div className="mt-6 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-64 animate-pulse rounded-xl bg-surface-2" />
              ))}
            </div>
          ) : (
            <div className="mt-6 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {visible.map((r) => {
                const tokens = templates?.find((t) => t.key === r.template_key)?.design_tokens;
                const tone = r.overall_score === null ? null : band(r.overall_score);
                return (
                  <div
                    key={r.id}
                    className="flex gap-4 rounded-xl border border-line-soft bg-surface-1 p-4"
                  >
                    {/* thumbnail */}
                    <button
                      onClick={() => openResume(r)}
                      className="shrink-0 overflow-hidden rounded-md border border-line-soft bg-paper"
                      style={{ width: 104, height: 147 }}
                      aria-label={`Open ${r.title}`}
                    >
                      <div className="pointer-events-none origin-top-left scale-[0.2]">
                        <ResumePreview data={r.structured_data} tokens={tokens} />
                      </div>
                    </button>

                    <div className="flex min-w-0 flex-1 flex-col">
                      <div className="flex items-start gap-1">
                        {renaming === r.id ? (
                          <input
                            autoFocus
                            value={draftTitle}
                            onChange={(e) => setDraftTitle(e.target.value)}
                            onBlur={() => submitRename(r.id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") submitRename(r.id);
                              if (e.key === "Escape") setRenaming(null);
                            }}
                            className="min-w-0 flex-1 rounded border border-brand bg-surface-2 px-1.5 py-0.5 text-sm font-semibold outline-none"
                            aria-label="Resume title"
                          />
                        ) : (
                          <button
                            onClick={() => openResume(r)}
                            className="min-w-0 flex-1 truncate text-left text-base font-semibold hover:text-brand-text"
                          >
                            {r.title}
                          </button>
                        )}
                        <button
                          onClick={() => {
                            setDraftTitle(r.title);
                            setRenaming(r.id);
                          }}
                          className="shrink-0 rounded p-1 text-content-muted hover:text-content"
                          aria-label="Rename"
                        >
                          <Pencil className="size-3.5" aria-hidden />
                        </button>
                      </div>

                      {r.tailored_for && (
                        <p className="truncate text-xs text-content-muted">
                          Tailored: {r.tailored_for}
                        </p>
                      )}
                      <p className="text-xs text-content-muted">
                        Last updated {timeAgo(r.updated_at)}
                      </p>

                      <button
                        onClick={() => openAnalysis(r)}
                        className="mt-3 flex w-full items-center gap-3 rounded-lg border border-line-soft px-3 py-2 text-left hover:border-brand-text"
                      >
                        <ScoreRing score={r.overall_score} size={48} />
                        <div className="min-w-0">
                          <p className="text-sm font-medium">
                            Resume Strength:{" "}
                            <span className={tone?.className}>
                              {tone?.label ?? "Not analysed yet"}
                            </span>
                          </p>
                          <p className="truncate text-xs text-content-muted">
                            {r.overall_score === null
                              ? "Run an analysis to see your score."
                              : "Open the editor to improve it."}
                          </p>
                        </div>
                      </button>

                      <div className="mt-3 flex items-center gap-2">
                        <Button
                          variant="outline"
                          className="flex-1"
                          onClick={() => startTailoring(r)}
                        >
                          <Target className="size-4" aria-hidden />
                          {r.kind === "tailored" ? "Continue Tailoring" : "Tailor to a job"}
                        </Button>

                        <div className="relative">
                          <button
                            onClick={() => setMenuFor(menuFor === r.id ? null : r.id)}
                            className="grid size-9 place-items-center rounded-full border border-line-soft text-content-muted hover:text-content"
                            aria-label={`More actions for ${r.title}`}
                            aria-expanded={menuFor === r.id}
                          >
                            <MoreHorizontal className="size-4" aria-hidden />
                          </button>
                          {menuFor === r.id && (
                            <>
                              {/* click-away */}
                              <button
                                className="fixed inset-0 z-10 cursor-default"
                                onClick={() => setMenuFor(null)}
                                aria-hidden
                                tabIndex={-1}
                              />
                              <div className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-lg border border-line-soft bg-surface-2 py-1 shadow-xl">
                                <button
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-surface-3"
                                  onClick={() => {
                                    setMenuFor(null);
                                    fork.mutate({
                                      resumeId: r.id,
                                      title: `${r.title} (copy)`,
                                    });
                                  }}
                                >
                                  <Copy className="size-4" aria-hidden />
                                  Duplicate
                                </button>
                                <button
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-surface-3"
                                  onClick={() => {
                                    setMenuFor(null);
                                    openResume(r);
                                  }}
                                >
                                  <FileText className="size-4" aria-hidden />
                                  Open editor
                                </button>
                                <button
                                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-reject-text hover:bg-surface-3"
                                  onClick={() => {
                                    setMenuFor(null);
                                    remove.mutate(r.id);
                                  }}
                                >
                                  <Trash2 className="size-4" aria-hidden />
                                  Delete
                                </button>
                              </div>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}

              {/* New Resume placeholder, as in the reference */}
              <button
                onClick={() => dispatch({ type: "openModal", modal: "import" })}
                className="flex min-h-[220px] gap-4 rounded-xl border border-dashed border-line p-4 text-left hover:border-brand-text"
              >
                <div
                  className="grid shrink-0 place-items-center rounded-md border border-dashed border-line bg-surface-2 text-content-muted"
                  style={{ width: 104, height: 147 }}
                >
                  <FilePlus2 className="size-7" aria-hidden />
                </div>
                <div>
                  <p className="text-base font-semibold">New Resume</p>
                  <p className="mt-1 text-sm text-content-muted">
                    Import a file or start from scratch, then tailor it to each job
                    description in minutes.
                  </p>
                  <span
                    className="mt-3 inline-block text-sm font-medium text-brand-text hover:underline"
                    onClick={(e) => {
                      e.stopPropagation();
                      create.mutate(undefined);
                    }}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.stopPropagation();
                        create.mutate(undefined);
                      }
                    }}
                  >
                    {create.isPending ? "Creating…" : "Start from scratch"}
                  </span>
                </div>
              </button>
            </div>
          )}

          {!isLoading && visible.length === 0 && needle && (
            <p className="mt-6 text-sm text-content-muted">
              No resumes match “{query}”.
            </p>
          )}

          {(fork.isPending || remove.isPending) && (
            <p className="mt-4 flex items-center gap-2 text-sm text-content-muted">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              Working…
            </p>
          )}
        </div>
      </main>

      <TailorModal
        open={tailorFor !== null}
        onOpenChange={(open) => !open && setTailorFor(null)}
        resumeId={tailorFor?.id ?? null}
        resumeTitle={tailorFor?.title ?? "this resume"}
        onStarted={(childId) => {
          setTailorFor(null);
          // Land on the CHILD the fork created, with its suggestions open.
          nav.workspace(childId, "tailor");
        }}
      />
    </div>
  );
}
