import { useEffect } from "react";
import { ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useUsers } from "@/api/queries";
import { useAppDispatch, useAppState } from "@/lib/AppState";
import { useNav } from "@/app/nav";

/**
 * Five seeded demo users, no auth. The selection is written to localStorage
 * by the reducer and read by client.ts as X-User-Id on every request.
 */
export function UserSwitcher() {
  const { activeUserId } = useAppState();
  const dispatch = useAppDispatch();
  const nav = useNav();
  const { data: users, isLoading } = useUsers();

  // Nothing selected on first run — adopt the first seeded user so the app is
  // usable immediately rather than 401-ing on every call.
  useEffect(() => {
    if (!activeUserId && users?.length) {
      dispatch({ type: "setUser", userId: users[0].id });
    }
  }, [activeUserId, users, dispatch]);

  const active = users?.find((u) => u.id === activeUserId);

  if (isLoading || !users?.length) {
    return (
      <div className="h-9 w-40 animate-pulse rounded-lg bg-surface-2" aria-hidden />
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex items-center gap-2 rounded-lg border border-line-soft bg-surface-2 py-1.5 pl-1.5 pr-2.5 text-sm text-content hover:border-brand-text">
        <span
          className="grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white"
          style={{ backgroundColor: active?.avatar_color || "#4737ff" }}
          aria-hidden
        >
          {active?.name?.[0] ?? "?"}
        </span>
        <span className="max-w-[9rem] truncate">{active?.name ?? "Select user"}</span>
        <ChevronDown className="size-4 text-content-muted" aria-hidden />
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-64">
        {users.map((u) => (
          <DropdownMenuItem
            key={u.id}
            onClick={() => {
              dispatch({ type: "setUser", userId: u.id });
              // Resume ids are per-user, so any :resumeId in the URL would
              // 404 under the new user. Go home rather than show a dead route.
              nav.library();
            }}
            className="gap-2"
          >
            <span
              className="grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white"
              style={{ backgroundColor: u.avatar_color }}
              aria-hidden
            >
              {u.name[0]}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm text-content">{u.name}</span>
              <span className="block truncate text-xs text-content-muted">
                {u.title}
              </span>
            </span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
