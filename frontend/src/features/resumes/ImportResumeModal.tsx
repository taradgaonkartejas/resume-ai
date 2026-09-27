import { useRef, useState } from "react";
import { FileText, Loader2, UploadCloud, X } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useUploadResume } from "@/api/queries";
import { ApiError } from "@/api/client";
import { cn } from "cn";

/**
 * Import Resume.
 *
 * ALLOWED_SUFFIXES in resume_service.py is {.pdf, .docx, .txt} — three, not
 * the two the reference screenshot advertises. Max size is settings
 * max_upload_mb = 10.
 *
 * Both limits are enforced client-side purely for a fast, specific error
 * message; the server re-checks and remains the authority.
 */

const ACCEPT = ".pdf,.docx,.txt";
const MAX_MB = 10;

function validate(file: File): string | null {
  const dot = file.name.lastIndexOf(".");
  const suffix = dot === -1 ? "" : file.name.slice(dot).toLowerCase();
  if (![".pdf", ".docx", ".txt"].includes(suffix)) {
    return `${suffix || file.name} is not supported. Use PDF, DOCX or TXT.`;
  }
  if (file.size > MAX_MB * 1024 * 1024) {
    return `File is ${(file.size / 1024 / 1024).toFixed(1)} MB. The limit is ${MAX_MB} MB.`;
  }
  return null;
}

export function ImportResumeModal({
  open,
  onOpenChange,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onImported: (resumeId: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Object URL for the PDF preview pane. Revoked when the file changes.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const upload = useUploadResume({
    onSuccess: (resume) => {
      // Upload parses inline, so by the time this resolves the resume is
      // already parsed. Surface a parse failure rather than silently
      // opening an empty editor.
      if (resume.parse_status === "failed") {
        setError(resume.parse_note || "The file uploaded but could not be parsed.");
        return;
      }
      reset();
      onOpenChange(false);
      onImported(resume.id);
    },
    onError: (err) => {
      setError(
        err instanceof ApiError ? err.message : "Upload failed. Is the backend running?",
      );
    },
  });

  function reset() {
    setTitle("");
    chooseFile(null);
    setError(null);
  }

  function chooseFile(next: File | null) {
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return next && next.name.toLowerCase().endsWith(".pdf")
        ? URL.createObjectURL(next)
        : null;
    });
    setFile(next);
  }

  function accept(next: File | undefined) {
    if (!next) return;
    const problem = validate(next);
    setError(problem);
    if (problem) return;
    chooseFile(next);
    // Default the name to the filename without its extension — the same
    // fallback the server applies when title is blank.
    if (!title) setTitle(next.name.replace(/\.[^.]+$/, ""));
  }

  const busy = upload.isPending;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return; // don't let a click-away abandon an in-flight upload
        if (!next) reset();
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Import Resume</DialogTitle>
          <DialogDescription>
            Upload a PDF, DOCX or TXT file. It is parsed into structured content
            you can edit, score and tailor.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5 md:grid-cols-2">
          {/* -------- left: name + dropzone -------- */}
          <div className="flex flex-col gap-3">
            <label className="text-sm font-medium text-content" htmlFor="resume-name">
              Resume name
            </label>
            <input
              id="resume-name"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Backend Engineer — 2026"
              disabled={busy}
              className="rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-content placeholder:text-content-muted focus-visible:border-ring focus-visible:outline-none disabled:opacity-50"
            />

            <div
              role="button"
              tabIndex={0}
              aria-label="Choose a resume file"
              onClick={() => !busy && inputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  inputRef.current?.click();
                }
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (!busy) accept(e.dataTransfer.files?.[0]);
              }}
              className={cn(
                "grid cursor-pointer place-items-center rounded-xl border-2 border-dashed px-4 py-9 text-center transition-colors",
                dragging
                  ? "border-brand-text bg-surface-2"
                  : "border-line bg-surface-2/50 hover:border-brand-text",
                busy && "pointer-events-none opacity-60",
              )}
            >
              <UploadCloud className="size-7 text-brand-text" aria-hidden />
              <p className="mt-2 text-sm font-medium text-content">
                Drag and drop, or click to browse
              </p>
              <p className="mt-0.5 text-xs text-content-muted">
                PDF, DOCX or TXT · up to {MAX_MB} MB
              </p>
            </div>

            <input
              ref={inputRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => accept(e.target.files?.[0])}
            />

            {file && (
              <div className="flex items-center gap-2 rounded-lg border border-line-soft bg-surface-2 px-3 py-2">
                <FileText className="size-4 shrink-0 text-brand-text" aria-hidden />
                <span className="truncate text-sm text-content">{file.name}</span>
                <span className="ml-auto shrink-0 text-xs text-content-muted">
                  {(file.size / 1024).toFixed(0)} KB
                </span>
                {!busy && (
                  <button
                    onClick={() => chooseFile(null)}
                    aria-label="Remove file"
                    className="rounded p-0.5 text-content-muted hover:text-content"
                  >
                    <X className="size-4" />
                  </button>
                )}
              </div>
            )}

            {error && (
              <p role="alert" className="text-sm text-reject-text">
                {error}
              </p>
            )}
          </div>

          {/* -------- right: preview -------- */}
          <div className="flex min-h-[260px] flex-col overflow-hidden rounded-xl border border-line-soft bg-surface-2">
            {previewUrl ? (
              // Browsers render PDFs natively. DOCX cannot be previewed
              // without a converter library, so it gets the icon treatment.
              <object
                data={previewUrl}
                type="application/pdf"
                className="h-full min-h-[260px] w-full"
                aria-label="PDF preview"
              >
                <div className="grid h-full place-items-center p-4 text-center text-sm text-content-muted">
                  Preview unavailable in this browser.
                </div>
              </object>
            ) : (
              <div className="grid flex-1 place-items-center p-6 text-center">
                <div>
                  <FileText
                    className="mx-auto size-8 text-content-muted opacity-60"
                    aria-hidden
                  />
                  <p className="mt-2 text-sm text-content-muted">
                    {file
                      ? `${file.name.split(".").pop()?.toUpperCase()} files cannot be previewed in the browser.`
                      : "No file selected."}
                  </p>
                  {file && (
                    <p className="mt-1 text-xs text-content-muted">
                      It will still be parsed normally.
                    </p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              reset();
              onOpenChange(false);
            }}
            disabled={busy}
          >
            Cancel
          </Button>
          <Button
            onClick={() => file && upload.mutate({ file, title })}
            disabled={!file || busy}
          >
            {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {busy ? "Parsing…" : "Import resume"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
