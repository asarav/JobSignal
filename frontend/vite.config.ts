import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    watch: {
      // Cargo rewrites `src-tauri/target` while Tauri compiles; watching it
      // crashes Vite with EBUSY on Windows. Match both separator styles.
      ignored: [/src-tauri/, /[\\/]target[\\/]/, /[\\/]dist[\\/]/],
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
