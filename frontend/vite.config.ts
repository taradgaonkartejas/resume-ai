import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,              // bind 0.0.0.0 so other devices on your LAN can load it
    port: 5173,
    strictPort: true,        // fail loudly instead of silently moving to 5174
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