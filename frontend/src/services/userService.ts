import { http } from "@/api/client";
import type { UserOut } from "./types";

/**
 * Mirrors backend/app/api/users.py.
 *
 * There is no auth. The active user is chosen in the UI and sent as
 * X-User-Id on every request; client.ts reads it from localStorage.
 */
export const userService = {
  /** GET /api/users — the 5 seeded demo users. */
  list: () => http.get<UserOut[]>("/users"),
};
