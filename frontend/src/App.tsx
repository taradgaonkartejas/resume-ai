import { AppRoutes } from "@/app/routes";
import { useNav } from "@/app/nav";
import { ImportResumeModal } from "@/features/resumes/ImportResumeModal";
import { useAppDispatch, useAppState } from "@/lib/AppState";

/**
 * App shell: routes plus the one modal that outlives them.
 *
 *   /resumes  ──import──▶  /resumes/:id/template  ──▶  /resumes/:id?tab=…
 *       ▲                                                    │
 *       └────────────────────────────────────────────────────┘
 *
 * Navigation lives in the URL (see app/routes.tsx). The reducer keeps only
 * what is genuinely UI state: active user, open modal, preview zoom.
 *
 * The import modal is hoisted here because it is reachable from both the
 * library and the workspace, and its success moves the user into the
 * template step — which would be awkward to coordinate from inside a route.
 */
function App() {
  const { modal } = useAppState();
  const dispatch = useAppDispatch();
  const nav = useNav();

  return (
    <>
      <AppRoutes />
      <ImportResumeModal
        open={modal === "import"}
        onOpenChange={(open) =>
          dispatch(open ? { type: "openModal", modal: "import" } : { type: "closeModal" })
        }
        onImported={(id) => {
          dispatch({ type: "closeModal" });
          nav.templateStep(id);
        }}
      />
    </>
  );
}

export default App;
