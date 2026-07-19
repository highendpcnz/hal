import { resolve } from "node:path";
import { defineConfig } from "vite";

export default defineConfig({
  // Relative base: chunks resolve against the entry's URL under /static/assets/.
  base: "",
  build: {
    target: "es2022",
    outDir: "static/assets",
    emptyOutDir: true,
    sourcemap: true,
    modulePreload: false,
    rollupOptions: {
      input: resolve(process.cwd(), "frontend/hal-optic.ts"),
      output: {
        // Stable, unhashed names: index.html references hal-optic.js directly
        // and the server's static mount handles caching via ETags.
        entryFileNames: "hal-optic.js",
        chunkFileNames: "[name].js",
        assetFileNames: "[name][extname]"
      }
    }
  }
});
