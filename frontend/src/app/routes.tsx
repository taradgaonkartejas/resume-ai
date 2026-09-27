import { Navigate, Route, Routes } from "react-router-dom";
import { MyResumes } from "@/features/resumes/MyResumes";
import { ChooseTemplate } from "@/features/templates/ChooseTemplate";
import { Workspace } from "@/features/workspace/Workspace";

/**
 * Route table.
 *
 * Navigation moved OUT of the AppState reducer and into the URL. The earlier
 * "no router, it's local-only" call was made when there were three flat views
 * and no nested state; adding chat gave the workspace a resume id AND a tab,
 * at which point a refresh losing your place stops being acceptable and a bug
 * report stops being reproducible.
 *
 * Navigation helpers live in app/nav.ts -- see the note there on why.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/resumes" replace />} />
      <Route path="/resumes" element={<MyResumes />} />
      <Route path="/resumes/:resumeId/template" element={<ChooseTemplate />} />
      <Route path="/resumes/:resumeId" element={<Workspace />} />
      {/* Unknown URL: back to the library rather than a blank screen. */}
      <Route path="*" element={<Navigate to="/resumes" replace />} />
    </Routes>
  );
}
