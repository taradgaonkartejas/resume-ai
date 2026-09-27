import {
  createContext,
  useContext,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import { getActiveUserId, setActiveUserId } from "@/api/client";
import {
  applyTheme,
  otherTheme,
  readTheme,
  writeTheme,
  type Theme,
} from "@/lib/theme";

/**
 * UI state only. Anything that lives in Postgres belongs to React Query —
 * see api/queries.ts. Keeping the split strict is what stops this reducer
 * growing into a hand-rolled cache.
 */

export type ModalName = "none" | "import" | "templates";
/**
 * Kept as a type because the workspace reads it from the URL and passes it
 * around; the VALUE now lives in the `?tab=` search param, not here.
 */
export type LeftTab = "analysis" | "tailor";

/**
 * UI state that is NOT navigation. Anything describing "where am I" moved to
 * the URL (app/routes.tsx); anything in Postgres belongs to React Query.
 *
 * activeUserId stays here on purpose: it is identity, not a location, and a
 * user id in the URL invites cross-user link confusion in a 5-user demo.
 */
export interface AppState {
  activeUserId: string | null;
  theme: Theme;
  modal: ModalName;
  zoom: number;
}

export type Action =
  | { type: "setUser"; userId: string }
  | { type: "openModal"; modal: ModalName }
  | { type: "closeModal" }
  | { type: "setZoom"; zoom: number }
  | { type: "toggleTheme" };

function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "setUser": {
      // client.ts reads this key for X-User-Id on every request, so the
      // write has to happen wherever the user changes.
      setActiveUserId(action.userId);
      // Theme is stored per user, so switching persona adopts their
      // preference rather than leaking the previous user's choice.
      const nextTheme = readTheme(action.userId);
      applyTheme(nextTheme);
      // NOTE: the caller is responsible for navigating back to the library.
      // Resume ids are scoped per user, so the route's :resumeId would 404
      // under the new user -- see UserSwitcher.
      return {
        ...state,
        activeUserId: action.userId,
        theme: nextTheme,
        modal: "none",
      };
    }
    case "openModal":
      return { ...state, modal: action.modal };
    case "closeModal":
      return { ...state, modal: "none" };
    case "setZoom":
      return { ...state, zoom: Math.min(150, Math.max(50, action.zoom)) };
    case "toggleTheme": {
      // Applied and persisted here rather than in an effect: React 19's
      // `react-hooks/set-state-in-effect` rule pushes DOM sync out of
      // effects, and the class must flip in the same frame as the click
      // or the toggle feels laggy.
      const theme = otherTheme(state.theme);
      applyTheme(theme);
      writeTheme(state.activeUserId, theme);
      return { ...state, theme };
    }
    default:
      return state;
  }
}

const bootUserId = getActiveUserId();

const initial: AppState = {
  activeUserId: bootUserId,
  // index.html's boot script already put the right class on <html>; this
  // just mirrors it into state so the toggle renders the correct icon.
  theme: readTheme(bootUserId),
  modal: "none",
  zoom: 100,
};

const StateCtx = createContext<AppState>(initial);
const DispatchCtx = createContext<Dispatch<Action>>(() => {});

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const value = useMemo(() => state, [state]);
  return (
    <StateCtx.Provider value={value}>
      <DispatchCtx.Provider value={dispatch}>{children}</DispatchCtx.Provider>
    </StateCtx.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAppState() {
  return useContext(StateCtx);
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAppDispatch() {
  return useContext(DispatchCtx);
}
