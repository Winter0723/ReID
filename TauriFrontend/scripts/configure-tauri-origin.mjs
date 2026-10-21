import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { localApiBase } from "./local-api.mjs";


const projectDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");


export function configureTauriOrigin({ production = false } = {}) {
  const configuredBase = process.env.VITE_MYREID_API_BASE_URL?.trim();
  if (production && !configuredBase) {
    throw new Error("VITE_MYREID_API_BASE_URL is required for a production Tauri build");
  }
  const parsed = new URL(configuredBase || localApiBase());
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("VITE_MYREID_API_BASE_URL must use HTTP or HTTPS");
  }
  if (parsed.username || parsed.password) {
    throw new Error("VITE_MYREID_API_BASE_URL must not contain credentials");
  }
  const isLocal = ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname);
  if (production && (parsed.protocol !== "https:" || isLocal)) {
    throw new Error("Production Tauri builds require a non-local HTTPS API origin");
  }

  const origin = parsed.origin;
  const websocketOrigin = origin.replace(/^http/, "ws");
  const capabilityPath = resolve(projectDir, "src-tauri/capabilities/api-origin.generated.json");
  const configPath = resolve(projectDir, "src-tauri/tauri.api.generated.conf.json");
  mkdirSync(dirname(capabilityPath), { recursive: true });
  writeFileSync(
    capabilityPath,
    `${JSON.stringify({
      "$schema": "../gen/schemas/desktop-schema.json",
      identifier: "api-origin",
      description: `Generated HTTP capability for ${origin}`,
      windows: ["main"],
      permissions: [{ identifier: "http:default", allow: [{ url: `${origin}/**` }] }],
    }, null, 2)}\n`,
    "utf8",
  );

  const csp = [
    "default-src 'self'",
    `connect-src 'self' ipc: http://ipc.localhost ${origin} ${websocketOrigin}`,
    `img-src 'self' asset: http://asset.localhost data: blob: ${origin}`,
    `media-src 'self' asset: http://asset.localhost blob: ${origin}`,
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "font-src 'self' data:",
  ].join("; ");
  writeFileSync(
    configPath,
    `${JSON.stringify({ app: { security: { csp } } }, null, 2)}\n`,
    "utf8",
  );
  return { configPath, origin };
}


if (process.argv[1] === fileURLToPath(import.meta.url)) {
  configureTauriOrigin({ production: process.argv.includes("--production") });
}
