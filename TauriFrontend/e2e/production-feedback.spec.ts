import { expect, test } from "@playwright/test";
import { preview, type PreviewServer } from "vite";

test.use({ baseURL: "http://127.0.0.1:41732" });
let server: PreviewServer;
test.afterEach(async ({ page }, testInfo) => {
  await page.screenshot({ path: testInfo.outputPath("verified.png") });
});
test.beforeAll(async () => {
  server = await preview({ configFile: "vite.config.ts", configLoader: "runner", preview: { host: "127.0.0.1", port: 41732, strictPort: true } });
});
test.afterAll(async () => { await new Promise<void>((resolve) => server.httpServer.close(() => resolve())); });

test("production login and verification code actions show feedback", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/v1/readyz", route => route.fulfill({ json: { status: "ready" } }));
  await page.route("**/api/v1/auth/login", route => route.fulfill({ status: 401, json: { detail: "用户名或密码错误" } }));
  await page.route("**/api/v1/auth/email-code/request", route => route.fulfill({ json: { status: "success", message: "验证码已发送，请查收邮箱", expires_in_seconds: 300 } }));
  await page.goto("/");
  await expect(page).toHaveTitle(/MyReID/);
  await page.getByRole("button", { name: "打开登录窗口" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByPlaceholder("用户名或邮箱").fill("tester");
  await dialog.getByPlaceholder("密码", { exact: true }).fill("wrong-password");
  await dialog.getByRole("button", { name: "登录系统" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("用户名或密码错误");
  await dialog.getByText("没有账号？立即注册").click();
  await dialog.getByPlaceholder("邮箱", { exact: true }).fill("tester@example.test");
  await dialog.getByRole("button", { name: "获取验证码" }).click();
  await expect(dialog.getByRole("status")).toHaveText("验证码已发送，请查收邮箱");
  await page.route("**/api/v1/auth/register", route => route.fulfill({ status: 400, json: { detail: "验证码无效或已过期" } }));
  await dialog.getByRole("button", { name: "注册账号", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("验证码无效或已过期");
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("production search renders its image before history finishes", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    localStorage.setItem("myreid.core.access", "test-access");
    localStorage.setItem("myreid.core.refresh", "test-refresh");
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: false }));
  });
  let searched = false;
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = { status: "ok" };
    if (path.endsWith("/readyz")) body = { status: "ready", queue_mode: "redis" };
    else if (path.endsWith("/auth/me")) body = { username: "tester", role: "operator" };
    else if (path === "/api/v1/projects") body = [{ id: 7, name: "检索验证项目", reid_model: { id: "clip", name: "CLIP-ReID", available: true, capabilities: { image: true, text: true } }, project_type: "media" }];
    else if (path.endsWith("/status")) body = { indexed_targets: 1 };
    else if (path.endsWith("/preview")) body = { previews: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/history")) {
      if (searched) { await new Promise(resolve => setTimeout(resolve, 3000)); }
      body = { history: [] };
    } else if (path.endsWith("/search/text")) {
      searched = true;
      body = { status: "success", task_id: 1, results: [{ id: 1, rank: 1, similarity: 0.95, thumbnail_url: "/api/v1/projects/7/targets/1/thumbnail?asset_expires=123&asset_signature=test", path: "", media_filename: "match.jpg" }] };
    } else if (path.endsWith("/thumbnail")) {
      await route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=", "base64") });
      return;
    }
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "打开检索结果", exact: true }).click();
  await page.getByPlaceholder("描述目标人物的衣着、外貌等特征").fill("red jacket");
  await page.getByRole("button", { name: "以文搜图", exact: true }).click();
  await expect(page.locator(".result-tile")).toBeVisible({ timeout: 2000 });
  await expect(page.locator(".result-tile img")).toHaveJSProperty("naturalWidth", 1);
  await expect(page.getByText("95%", { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
});
