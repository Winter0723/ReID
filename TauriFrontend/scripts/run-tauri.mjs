import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { configureTauriOrigin } from "./configure-tauri-origin.mjs";


const command = process.argv[2];
if (!new Set(["dev", "build"]).has(command)) {
  throw new Error("Expected Tauri command 'dev' or 'build'");
}
const projectDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { configPath } = configureTauriOrigin({ production: command === "build" });
const tauriCli = resolve(projectDir, "node_modules/@tauri-apps/cli/tauri.js");
const result = spawnSync(
  process.execPath,
  [tauriCli, command, "--config", configPath, ...process.argv.slice(3)],
  { cwd: projectDir, stdio: "inherit", env: process.env },
);
if (result.error) throw result.error;
process.exit(result.status ?? 1);
