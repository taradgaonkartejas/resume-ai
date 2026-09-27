import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App.tsx";
import { AppStateProvider } from "./lib/AppState.tsx";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A local 5-user app: refetching every time the window regains focus is
      // noise, not freshness.
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      // 404s and 422s will not fix themselves; only retry once, and never on
      // a client error.
      retry: (failureCount, error) => {
        const status = (error as { status?: number })?.status ?? 0;
        if (status >= 400 && status < 500) return false;
        return failureCount < 1;
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppStateProvider>
          <App />
        </AppStateProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
