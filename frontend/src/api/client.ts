const USER_KEY = "resumeai.activeUserId";

export function getActiveUserId(): string | null {
  return localStorage.getItem(USER_KEY);
}

export function setActiveUserId(id: string): void {
  localStorage.setItem(USER_KEY, id);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const userId = getActiveUserId();
  if (userId) headers.set("X-User-Id", userId);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  // Relative URL: the Vite proxy (dev) or the same origin (prod) resolves it.
  const res = await fetch(`/api${path}`, { ...init, headers });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const api = {
  get:   <T>(p: string) => request<T>(p),
  post:  <T>(p: string, body?: unknown) =>
           request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put:   <T>(p: string, body: unknown) =>
           request<T>(p, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T>(p: string, body: unknown) =>
           request<T>(p, { method: "PATCH", body: JSON.stringify(body) }),
  del:   <T>(p: string) => request<T>(p, { method: "DELETE" }),

  upload: <T>(p: string, file: File, fields: Record<string, string> = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
    return request<T>(p, { method: "POST", body: fd });
  },

  // Exports stream binary; bypass the JSON path.
  download: async (resumeId: string, format: "pdf" | "docx" | "txt") => {
    const headers = new Headers();
    const userId = getActiveUserId();
    if (userId) headers.set("X-User-Id", userId);
    const res = await fetch(`/api/resumes/${resumeId}/export?format=${format}`, { headers });
    if (!res.ok) throw new ApiError(res.status, "Export failed");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `resume.${format}`;
    a.click();
    URL.revokeObjectURL(url);
  },
};