import { resolve } from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    target: "es2022",
    outDir: "static/assets",
    emptyOutDir: true,
    sourcemap: true,
    lib: {
      entry: resolve(process.cwd(), "frontend/hal-optic.ts"),
      formats: ["es"],
      fileName: () => "hal-optic.js"
    }
  }
});
