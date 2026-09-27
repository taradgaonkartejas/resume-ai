import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // shadcn generates "@/components/..." imports. Vite needs this to
    // resolve them at build time; tsconfig needs it for type-checking.
    //
    // import.meta.dirname, not __dirname: Vite 8 warns that __dirname is
    // unsupported by configLoader:'native', which becomes the default in a
    // future major. This is the forward-compatible spelling.
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // Vite 8 rejects Host headers it does not recognise (DNS-rebinding guard).
    // Local dev on localhost:5173 is unaffected; this only additionally
    // permits tunnelled/preview hostnames such as *.e2b.app.
    allowedHosts: [".e2b.app", "localhost", "127.0.0.1"],
    proxy: {
      // The browser only ever talks to :5173. Vite forwards /api to FastAPI,
      // so there is no CORS negotiation and no backend URL in client code.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});