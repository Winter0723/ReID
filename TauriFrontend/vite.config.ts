import { defineConfig } from "vite";
// @ts-expect-error Local Node configuration helper.
import { localApiBase } from "./scripts/local-api.mjs";
import vue from "@vitejs/plugin-vue";
import tailwindcss from "@tailwindcss/vite";

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;
// @ts-expect-error process is a nodejs global
const platform = process.env.TAURI_ENV_PLATFORM;
// @ts-expect-error process is a nodejs global
const debug = process.env.TAURI_ENV_DEBUG;

// https://vite.dev/config/
export default defineConfig(async ({ mode }) => ({
  define: { "import.meta.env.VITE_MYREID_LOCAL_API_BASE_URL": JSON.stringify(localApiBase(mode)) },
  plugins: [vue(), tailwindcss()],
  optimizeDeps: {
    noDiscovery: true,
    include: [],
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 5173,
    strictPort: true,
    host: host || "127.0.0.1",
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
    proxy: {
      "/api": {
        ws: true,
        target: localApiBase(mode),
        changeOrigin: true,
      },
      "/objects": {
        target: localApiBase(mode),
        changeOrigin: true,
      },
      "/gallery": {
        target: localApiBase(mode),
        changeOrigin: true,
      },
      "/metrics": {
        target: localApiBase(mode),
        changeOrigin: true,
      },
    },
  },
  envPrefix: ["VITE_", "TAURI_ENV_*"],
  build: {
    target: platform === "windows" ? "chrome105" : "safari13",
    minify: debug ? false : "esbuild",
    sourcemap: Boolean(debug),
  },
}));
