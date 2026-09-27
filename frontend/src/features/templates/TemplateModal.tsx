import { useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useApplyTemplate, useTemplates } from "@/api/queries";
import type { StructuredData } from "@/services";
import { TemplateGrid } from "./TemplateGrid";

/**
 * Change the template of an existing resume.
 *
 * The post-upload step uses the same TemplateGrid, so the two cannot drift.
 * Applying is non-destructive: content and layout are decoupled server-side,
 * so only template_key moves.
 */
export function TemplateModal({
  open,
  onOpenChange,
  resumeId,
  data,
  currentKey,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resumeId: string | null;
  data: StructuredData;
  currentKey: string;
}) {
  const { data: templates, isLoading, isError, error } = useTemplates();
  const apply = useApplyTemplate(resumeId);
  const [selected, setSelected] = useState(currentKey);

  // Re-sync when the modal reopens. React's "adjust state on prop change"
  // pattern, done during render rather than in an effect, so there is no
  // cascading second render and no flash of the stale selection.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setSelected(currentKey);
  }

  const dirty = selected !== currentKey;

  return (
    <Dialog open={open} onOpenChange={(next) => !apply.isPending && onOpenChange(next)}>
      <DialogContent className="sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>Choose Resume Template</DialogTitle>
          <DialogDescription>
            Previews use your real content. Switching layout never changes your
            text, so you can move between templates freely.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="grid h-64 place-items-center">
            <Loader2 className="size-6 animate-spin text-brand-text" aria-hidden />
          </div>
        ) : isError ? (
          <div className="grid h-64 place-items-center px-6 text-center">
            <p className="text-sm text-reject-text">
              Could not load templates. {(error as Error)?.message}
            </p>
          </div>
        ) : (
          <div className="max-h-[62vh] overflow-y-auto">
            <TemplateGrid
              templates={templates ?? []}
              data={data}
              selected={selected}
              currentKey={currentKey}
              onSelect={setSelected}
            />
          </div>
        )}

        {apply.isError && (
          <p role="alert" className="text-sm text-reject-text">
            {(apply.error as Error)?.message ?? "Could not apply template."}
          </p>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={apply.isPending}
          >
            Cancel
          </Button>
          <Button
            disabled={!dirty || apply.isPending || !resumeId}
            onClick={() =>
              apply.mutate(selected, { onSuccess: () => onOpenChange(false) })
            }
          >
            {apply.isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
            {dirty ? "Apply Template" : "Applied"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
