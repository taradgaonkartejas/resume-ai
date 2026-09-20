import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // shadcn generates "@/components/..." imports. Vite needs this to
    // resolve them at build time; tsconfig needs it for type-checking.
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
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