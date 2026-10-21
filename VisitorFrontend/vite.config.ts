import { defineConfig, loadEnv } from "vite";
// @ts-expect-error Node built-in used only by the Vite configuration.
import { fileURLToPath } from "node:url";
import vue from "@vitejs/plugin-vue";

const root = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  const value = loadEnv(mode, root, "MYREID_API_PORT").MYREID_API_PORT || "2889";
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("MYREID_API_PORT must be between 1 and 65535");
  }

  return {
    plugins: [vue()],
    base: "/visitor/",
    server: {
      host: "0.0.0.0",
      port: 5174,
      strictPort: true,
      proxy: { "/api": { target: `http://127.0.0.1:${port}`, changeOrigin: true } },
    },
  };
});
