import { useState } from "react";
import { Check, Download, Loader2 } from "lucide-react";
import { ApiError } from "@/api/client";
import { exportService, type ExportFormat } from "@/services";
import { Button } from "@/components/ui/button";
import { cn } from "cn";

/**
 * Download a resume as PDF, DOCX or plain text.
 *
 * The endpoint and the blob-saving service already existed; nothing in the
 * app ever called them, so there was no way to get a finished resume out.
 *
 * Always enabled for a parsed resume. Gating export on "analysed" or "no
 * pending suggestions" would withhold the user's own document on the
 * strength of advice they are entitled to ignore.
 *
 * The menu is hand-rolled to match the one in MyResumes rather than using
 * components/ui/dropdown-menu: that primitive is Base UI, is not used
 * anywhere else in the app, and takes `render` rather than `asChild`.
 */
const FORMATS: { format: ExportFormat; label: string; hint: string }[] = [
  { format: "pdf", label: "PDF", hint: "Formatted, for sending" },
  { format: "docx", label: "Word (.docx)", hint: "Editable" },
  { format: "txt", label: "Plain text", hint: "For paste-in forms" },
];

export function ExportButton({
  resumeId,
  title,
  variant = "outline",
  size = "default",
  className,
  label = "Export",
}: {
  resumeId: string;
  /** Fallback filename when the server sends no Content-Disposition. */
  title?: string;
  variant?: "default" | "outline" | "ghost" | "secondary";
  size?: "sm" | "default" | "icon";
  className?: string;
  /** Pass null for an icon-only button in tight toolbars. */
  label?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(format: ExportFormat) {
    setOpen(false);
    setBusy(format);
    setError(null);
    try {
      await exportService.download(resumeId, format, title || "resume");
      setDone(true);
      window.setTimeout(() => setDone(false), 2000);
    } catch (err) {
      // A failed download is otherwise completely silent -- the browser
      // shows nothing when the request rejects, so the button says it.
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not build the file. Try again.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={cn("relative", className)}>
      <Button
        variant={variant}
        size={label ? size : "icon"}
        disabled={busy !== null}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label ? undefined : "Export resume"}
        onClick={(e) => {
          // The resume card behind this is itself a button; without this the
          // click opens the resume instead of the menu.
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" aria-hidden />
        ) : done ? (
          <Check className="size-4 text-accept-text" aria-hidden />
        ) : (
          <Download className="size-4" aria-hidden />
        )}
        {label}
      </Button>

      {open && (
        <>
          {/* click-away */}
          <button
            className="fixed inset-0 z-10 cursor-default"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
            }}
            aria-hidden
            tabIndex={-1}
          />
          <div
            role="menu"
            className="absolute right-0 z-20 mt-1 w-52 overflow-hidden rounded-lg border border-line-soft bg-surface-2 py-1 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {FORMATS.map((f) => (
              <button
                key={f.format}
                role="menuitem"
                className="block w-full px-3 py-2 text-left hover:bg-surface-3"
                onClick={() => void run(f.format)}
              >
                <span className="block text-sm text-content">{f.label}</span>
                <span className="block text-[11px] text-content-muted">
                  {f.hint}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {error && (
        <p role="alert" className="mt-1 max-w-48 text-[11px] text-reject-text">
          {error}
        </p>
      )}
    </div>
  );
}
