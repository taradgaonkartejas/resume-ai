/**
 * Renders the REAL EventRow/ScoreCard components to a static HTML page with
 * the REAL built stylesheet inlined, so the styling can be looked at without
 * running Postgres, the API and the dev server.
 *
 * It imports the shipped component rather than re-creating its markup -- a
 * hand-written mock would be free to look good while the component did not.
 */
import { build } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

const root = process.cwd();

await build({
  configFile: false,
  root,
  resolve: { alias: { "@": path.resolve(root, "src") } },
  plugins: [react()],
  logLevel: "error",
  build: {
    ssr: "scripts/_preview/entry.tsx",
    outDir: "scripts/_preview/out",
    emptyOutDir: true,
    rollupOptions: { output: { entryFileNames: "entry.mjs" } },
  },
});

// The component graph reaches AppState, which reads localStorage at module
// scope. Stub the browser globals it touches rather than refactoring app code
// to suit a preview script.
const store = new Map();
globalThis.localStorage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
  clear: () => store.clear(),
};
globalThis.matchMedia ??= () => ({ matches: true, addEventListener() {}, removeEventListener() {} });

const { render } = await import(path.resolve(root, "scripts/_preview/out/entry.mjs"));
const body = render();

const cssFile = fs.readdirSync(path.join(root, "dist/assets")).find(f => f.endsWith(".css"));
const css = fs.readFileSync(path.join(root, "dist/assets", cssFile), "utf8");

const page = `<!doctype html>
<html lang="en" class="dark">
<head><meta charset="utf-8"><title>Chat sync - event styling</title>
<style>${css}</style>
<style>
  body { margin:0; background: var(--canvas, #101018); }
  .wrap { max-width: 480px; margin: 0 auto; border-left:1px solid var(--line-soft);
          border-right:1px solid var(--line-soft); min-height:100vh;
          background: var(--surface-1); }
  .lbl { font: 600 11px/1 ui-sans-serif, system-ui; letter-spacing:.08em;
         text-transform:uppercase; color: var(--text-muted);
         padding:14px 20px 0; }
</style>
</head>
<body class="bg-canvas text-content">
  <div class="wrap">
    <div class="lbl">Chat pane &mdash; mirrored events (dark)</div>
    ${body}
  </div>
</body></html>`;

fs.writeFileSync(path.join(root, "../chat-events-preview.html"), page);
console.log("wrote chat-events-preview.html (" + page.length + " bytes)");
