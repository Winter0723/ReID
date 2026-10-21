import { loadEnv } from "vite";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
export function localApiBase(mode = "development") {
  const value = process.env.MYREID_API_PORT || loadEnv(mode, root, "MYREID_API_PORT").MYREID_API_PORT || "2889";
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("MYREID_API_PORT must be between 1 and 65535");
  return `http://127.0.0.1:${port}`;
}
