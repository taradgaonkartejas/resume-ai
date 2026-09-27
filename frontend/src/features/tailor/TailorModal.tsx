import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Sparkles } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useStartTailoring } from "@/api/queries";
import { systemService } from "@/services";

/**
 * Collects the job description, then starts a tailoring session.
 *
 * Tailoring FORKS server-side, so the session comes back pointing at a NEW
 * resume. onStarted receives that child's id — the caller must navigate to
 * it, otherwise the user keeps editing the untouched base and wonders why
 * nothing changed.
 */
export function TailorModal({
  open,
  onOpenChange,
  resumeId,
  resumeTitle,
  onStarted,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resumeId: string | null;
  resumeTitle: string;
  onStarted: (childResumeId: string, sessionId: string) => void;
}) {
  const [jdTitle, setJdTitle] = useState("");
  const [jdContent, setJdContent] = useState("");

  const start = useStartTailoring({
    onSuccess: (session) => {
      setJdTitle("");
      setJdContent("");
      onOpenChange(false);
      onStarted(session.resume_id, session.id);
    },
  });

  // Loaded lazily: only fetched when the user asks for the sample.
  const [wantSample, setWantSample] = useState(false);
  const sample = useQuery({
    queryKey: ["sample-jd"],
    queryFn: systemService.sampleJd,
    enabled: wantSample,
    staleTime: Infinity,
  });
  if (wantSample && sample.data && !jdContent) {
    setWantSample(false);
    setJdTitle(sample.data.title);
    setJdContent(sample.data.content);
  }

  const ready = jdContent.trim().length > 0 && Boolean(resumeId);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !start.isPending && onOpenChange(next)}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Tailor to a job</DialogTitle>
          <DialogDescription>
            Paste the job description. This creates a <strong>new</strong> tailored
            copy — “{resumeTitle}” is never modified.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label
              htmlFor="jd-title"
              className="mb-1 block text-xs font-medium text-content-muted"
            >
              Role and company
            </label>
            <input
              id="jd-title"
              value={jdTitle}
              onChange={(e) => setJdTitle(e.target.value)}
              placeholder="Senior Software Engineer at Cognizant"
              className="w-full rounded-lg border border-line-soft bg-surface-1 px-3 py-2 text-sm outline-none placeholder:text-content-muted focus:border-brand-text"
            />
            <p className="mt-1 text-[11px] text-content-muted">
              Used as the card subtitle in My Resumes.
            </p>
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label htmlFor="jd-body" className="text-xs font-medium text-content-muted">
                Job description
              </label>
              <button
                onClick={() => setWantSample(true)}
                className="text-[11px] font-medium text-brand-text hover:underline"
                type="button"
              >
                {sample.isFetching ? "Loading…" : "Use sample JD"}
              </button>
            </div>
            <textarea
              id="jd-body"
              value={jdContent}
              onChange={(e) => setJdContent(e.target.value)}
              rows={10}
              placeholder="Paste the full job description here…"
              className="w-full resize-y rounded-lg border border-line-soft bg-surface-1 px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-content-muted focus:border-brand-text"
            />
          </div>

          {start.isError && (
            <p role="alert" className="text-sm text-reject-text">
              {(start.error as Error)?.message ?? "Could not start tailoring."}
            </p>
          )}

          {start.isPending && (
            <p className="flex items-center gap-2 rounded-lg border border-line-soft bg-surface-2 px-3 py-2 text-sm text-content-muted">
              <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden />
              Running the JD analyst, writer and critic agents. On the free tier
              this can take a minute.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={start.isPending}
          >
            Cancel
          </Button>
          <Button
            disabled={!ready || start.isPending}
            onClick={() =>
              start.mutate({
                resumeId: resumeId!,
                jd: { jd_title: jdTitle, jd_content: jdContent },
              })
            }
          >
            {start.isPending ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="size-4" aria-hidden />
            )}
            Create tailored resume
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
