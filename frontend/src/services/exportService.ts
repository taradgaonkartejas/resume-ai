import { http } from "@/api/client";
import type { ExportFormat } from "./types";

/**
 * Mirrors backend/app/api/exports.py.
 *
 * The endpoint STREAMS BYTES rather than returning a presigned URL, because
 * presigned URLs do not exist when storage has fallen back to local disk
 * (exports.py says so explicitly). So this is a blob download, not a redirect.
 */
export const exportService = {
  /** GET /api/resumes/{id}/export?format=pdf|docx|txt */
  fetch: (resumeId: string, format: ExportFormat = "pdf") =>
    http.blob(`/resumes/${resumeId}/export`, { query: { format } }),

  /**
   * Fetch and save. Prefers the server's Content-Disposition filename, which
   * is built from the resume title; falls back to a sensible local name.
   *
   * The object URL is revoked on the next tick — revoking synchronously can
   * cancel the download in some browsers before it starts.
   */
  download: async (
    resumeId: string,
    format: ExportFormat = "pdf",
    fallbackName = "resume",
  ): Promise<string> => {
    const { blob, filename } = await exportService.fetch(resumeId, format);
    const name = filename ?? `${fallbackName}.${format}`;

    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);

    return name;
  },
};
