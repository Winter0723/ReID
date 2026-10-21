import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { join } from "node:path";

let server: ViteDevServer;
const url = "http://127.0.0.1:41739";
test.beforeAll(async () => {
  server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error",
    server: { host: "127.0.0.1", port: 41739, strictPort: true } });
  await server.listen();
});
test.afterAll(async () => { await server?.close(); });

for (const scope of ["core", "scenic"] as const) {
  test(`${scope} password Enter submits once and completes login`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", event => {
      if (["error", "warning"].includes(event.type())) errors.push(event.text());
    });
    let attempts = 0;
    let releaseLogin = () => {};
    const loginGate = new Promise<void>(resolve => { releaseLogin = resolve; });
    await page.route("**/api/v1/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = { status: "ok" };
      if (path.endsWith("/auth/login")) {
        expect(path).toBe(scope === "core" ? "/api/v1/auth/login" : "/api/v1/scenic/auth/login");
        expect(route.request().postDataJSON()).toEqual({ username: "operator", password: "test-password" });
        attempts++;
        await loginGate;
        body = { access_token: "test-access", refresh_token: "test-refresh", token_type: "bearer" };
      } else if (path.endsWith("/auth/me")) body = { username: "operator", role: "operator" };
      else if (path.endsWith("/readyz")) body = { status: "ready", queue_ready: true, queue_mode: "local" };
      else if (path.endsWith("/projects")) body = [];
      else if (path.endsWith("/cameras")) body = { devices: [] };
      else if (path.endsWith("/history")) body = { history: [] };
      await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
    });
    await page.goto(url);
    await expect(page).toHaveURL(`${url}/`);
    await expect(page).toHaveTitle("MyReID Vision");
    if (scope === "core") await page.getByRole("button", { name: "打开登录窗口" }).click();
    else await page.getByTitle("切换到景区运营").click();
    const form = scope === "core" ? page.getByRole("dialog") : page.locator(".login-card");
    await expect(form).toBeVisible();
    await form.getByPlaceholder("用户名或邮箱", { exact: true }).fill("operator");
    const password = form.getByPlaceholder("密码", { exact: true });
    await password.fill("test-password");
    await password.press("Enter");
    await expect.poll(() => attempts).toBe(1);
    await expect(form.getByRole("button", { name: scope === "core" ? "登录系统" : "登录景区后台", exact: true })).toBeDisabled();
    await password.press("Enter");
    if (process.env.MYREID_QA_DIR) await page.screenshot({ path: join(process.env.MYREID_QA_DIR, `login-enter-${scope}.png`) });
    releaseLogin();
    await expect(form).not.toBeVisible();
    await expect(page.locator(".sidebar-account")).toContainText("operator");
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    expect(attempts).toBe(1);
    expect(errors).toEqual([]);
  });
}
