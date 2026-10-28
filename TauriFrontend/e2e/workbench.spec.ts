import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";


let devServer: ViteDevServer;

test.beforeAll(async () => {
  devServer = await createServer({
    configFile: "vite.config.ts",
    configLoader: "runner",
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: 41731,
      strictPort: true,
    },
  });
  await devServer.listen();
});

test.afterAll(async () => {
  await devServer?.close();
});


test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/readyz", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        status: "degraded",
        queue_ready: true,
        queue_mode: "local",
        degraded_reasons: ["queue_local_fallback"],
        clip_loaded: true,
        yolo_loaded: true,
      }),
    });
  });
});


test("unauthenticated scope switching is quiet and fits the minimum window", async ({ page }) => {
  const protectedRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (/\/(projects|search\/history|observability|audit)(\/|$)/.test(path)) {
      protectedRequests.push(path);
    }
  });
  await page.goto("/");
  await expect(page.getByTitle("切换到景区运营")).toBeVisible();
  await page.getByTitle("切换到景区运营").click();
  await expect(page.locator("main.scenic-shell")).toBeVisible();
  await expect(page.getByText("登录状态已过期")).toHaveCount(0);
  await page.getByTitle("返回基础系统").click();
  await expect(page.getByTitle("切换到景区运营")).toBeVisible();

  const overflow = await page.evaluate(() => ({
    body: document.body.scrollWidth - window.innerWidth,
    root: document.documentElement.scrollWidth - window.innerWidth,
  }));
  expect(overflow.body).toBeLessThanOrEqual(0);
  expect(overflow.root).toBeLessThanOrEqual(0);
  await expect(page.getByText("本地处理", { exact: true })).toBeVisible();
  expect(protectedRequests).toEqual([]);
});


test("authenticated users without a project get guidance and disabled actions", async ({ page }) => {
  const requestedPaths: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem("myreid.core.access", "operator-access");
    localStorage.setItem("myreid.core.refresh", "operator-refresh");
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    requestedPaths.push(path);
    let body: unknown = { status: "ok" };
    if (path === "/api/v1/readyz") {
      body = { status: "degraded", queue_ready: true, queue_mode: "local", clip_loaded: true };
    } else if (path === "/api/v1/auth/me") {
      body = { username: "operator", role: "operator", email: "operator@example.test" };
    } else if (path === "/api/v1/projects") {
      body = [];
    } else if (path === "/api/v1/cameras") {
      body = { status: "ok", devices: [], message: "none" };
    } else if (path === "/api/v1/metrics") {
      body = {};
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/");
  await expect(page.getByText("请先创建或选择项目", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "重建当前项目索引" })).toBeDisabled();
  await page.getByRole("button", { name: "打开检索结果", exact: true }).click();
  await page.getByLabel("选择用于检索的图片").setInputFiles({
    name: "query.png",
    mimeType: "image/png",
    buffer: Buffer.from("not-a-real-image"),
  });
  await expect(page.getByText("query.png", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "以图搜图" })).toBeDisabled();
  expect(requestedPaths).not.toContain("/api/v1/observability");
});


test("ordinary core users see current-project audit instead of global observability", async ({ page }) => {
  const requestedPaths: string[] = [];
  await page.addInitScript(() => {
    localStorage.setItem("myreid.core.access", "operator-access");
    localStorage.setItem("myreid.core.refresh", "operator-refresh");
  });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    requestedPaths.push(path);
    let body: unknown = { status: "ok" };
    if (path === "/api/v1/readyz") {
      body = { status: "ready", queue_ready: true, queue_mode: "redis", clip_loaded: true };
    } else if (path === "/api/v1/auth/me") {
      body = { username: "operator", role: "operator", email: "operator@example.test" };
    } else if (path === "/api/v1/projects") {
      body = [{ id: 7, name: "审计项目", description: "", reid_model: { id: "clip", name: "CLIP-ReID", available: true, capabilities: { image: true, text: true } }, project_type: "media", owner_username: "operator", created_at: "2026-08-31" }];
    } else if (path === "/api/v1/projects/7/status") {
      body = { status: "ready", indexed_targets: 2 };
    } else if (path === "/api/v1/projects/7/preview") {
      body = { previews: [] };
    } else if (path === "/api/v1/projects/7/jobs") {
      body = { status: "ok", jobs: [] };
    } else if (path === "/api/v1/projects/7/media") {
      body = { status: "ok", media: [] };
    } else if (path === "/api/v1/projects/7/audit") {
      body = { status: "ok", audit: [{ id: 5, action: "media.uploaded", detail: "visitor.jpg", created_at: "2026-08-31T10:00:00Z" }] };
    } else if (path === "/api/v1/history") {
      body = { status: "ok", history: [] };
    } else if (path === "/api/v1/cameras") {
      body = { status: "ok", devices: [], message: "none" };
    } else if (path === "/api/v1/metrics") {
      body = {};
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "打开审计日志与系统事件" }).click();
  await expect(page.getByText("media.uploaded", { exact: true })).toBeVisible();
  await expect(page.getByText("visitor.jpg", { exact: true })).toBeVisible();
  expect(requestedPaths).toContain("/api/v1/projects/7/audit");
  expect(requestedPaths).not.toContain("/api/v1/observability");
});
