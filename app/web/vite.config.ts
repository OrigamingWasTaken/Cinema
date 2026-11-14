import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// The FastAPI app serves the build from app/cinema/static (not committed).
// `npm run dev` proxies the API to a backend on :8888.
export default defineConfig({
  plugins: [svelte()],
  base: "./",
  build: {
    outDir: "../cinema/static",
    emptyOutDir: true,
    target: ["es2022", "safari15"],
    assetsInlineLimit: 0,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8888" },
  },
});
