import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";

let server: ViteDevServer;
test.beforeAll(async () => {
  server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error",
    server: { host: "127.0.0.1", port: 41731, strictPort: true } });
  await server.listen();
});
test.afterAll(async () => { await server?.close(); });

test("create a model-bound project, disable text-only controls and refresh discovery", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const clip = { id: "clipreid-msmt17", name: "DSCL-ReID MSMT17", dimension: 512, available: true, default: true, capabilities: { image: true, text: true } };
  const mgd = { id: "mgd", name: "MGD-DPM Market2MSMT", dimension: 1280, available: true, capabilities: { image: true, text: false } };
  const projects: unknown[] = [];
  let discovery = 0;
  let created: Record<string, unknown> = {};
  await page.addInitScript(() => {
    localStorage.setItem("myreid.core.access", "test-token");
    localStorage.setItem("myreid.core.refresh", "test-refresh");
  });
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/v1/auth/me") body = { username: "operator", role: "operator" };
    else if (path === "/api/v1/readyz") body = { status: "ready", models_ready: true, queue_ready: true };
    else if (path === "/api/v1/models") {
      discovery++;
      if (discovery === 1) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
        return;
      }
      body = [clip, mgd, ...(discovery >= 3 ? [
        { ...mgd, id: "mgd-second", name: "MGD-DPM Second" },
        { ...mgd, id: "mgd-third", name: "MGD-DPM Third" },
      ] : []), { id: "missing", name: "缺失权重", available: false, unavailable_reason: "模型权重文件不存在" }];
    } else if (path === "/api/v1/projects") {
      if (route.request().method() === "POST") {
        created = route.request().postDataJSON();
        const project = { ...created, id: 1, owner_username: "operator", created_at: "2026-10-26", reid_model: mgd };
        projects.push(project);
        body = project;
      } else body = projects;
    } else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/audit")) body = { audit: [] };
    else if (path.endsWith("/history")) body = { history: [] };
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/preview")) body = { previews: [] };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/");
  await expect(page).toHaveTitle(/MyReID|ReID/i);
  await page.getByRole("button", { name: /打开项目管理：/ }).click();
  await expect(page.getByRole("alert")).toContainText("当前后端缺少多模型接口");
  await page.getByRole("button", { name: "刷新模型列表" }).click();
  await expect(page.getByLabel("项目模型")).toHaveValue(clip.id);
  await expect(page.getByRole("option", { name: /缺失权重/ })).toHaveAttribute("disabled", "");
  await page.getByRole("button", { name: "刷新模型列表" }).click();
  await expect.poll(() => discovery).toBe(3);
  await expect(page.getByRole("option", { name: /MGD-DPM Second/ })).toHaveCount(1);
  await expect(page.getByRole("option", { name: /MGD-DPM Third/ })).toHaveCount(1);
  await page.getByLabel("项目模型").selectOption("mgd");
  await page.getByRole("button", { name: "刷新模型列表" }).click();
  await expect(page.getByLabel("项目模型")).toHaveValue("mgd");
  await expect(page.getByText("支持图片检索，不支持文本检索", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认创建" }).click();
  await expect.poll(() => created.reid_model_id).toBe("mgd");
  await expect(page.locator(".workspace-topbar")).toContainText(mgd.name);
  await page.getByRole("button", { name: "打开检索结果", exact: true }).click();
  await expect(page.getByRole("button", { name: "以文搜图" })).toBeDisabled();
  await expect(page.getByPlaceholder("描述目标人物的衣着、外貌等特征")).toBeDisabled();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  if (process.env.MODEL_QA_SCREENSHOT) await page.screenshot({ path: process.env.MODEL_QA_SCREENSHOT });
  expect(errors).toEqual([]);
});
