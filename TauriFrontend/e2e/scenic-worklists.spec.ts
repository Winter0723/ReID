import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { tmpdir } from "node:os";
import { join } from "node:path";
let server: ViteDevServer;
const url = "http://127.0.0.1:41734";
test.beforeAll(async () => { server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error", server: { host: "127.0.0.1", port: 41734, strictPort: true } }); await server.listen(); });
test.afterAll(async () => { await server?.close(); });

test("finds older tourists across pages, preserves queues across tabs and scopes history and audit", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem("myreid.scenic.access", "fixture-token"); localStorage.setItem("myreid.scenic.refresh", "fixture-refresh");
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: false }));
  });
  const tourists = Array.from({ length: 35 }, (_, index) => ({ id: index + 1, ticket_code: `T-${String(index + 1).padStart(3, "0")}`, visitor_label: `游客 ${index + 1}`, status: "matched", work_queues: index % 2 ? ["needs_review"] : ["needs_vlog"], segments: [], vlog_jobs: [], orders: [] }));
  const listRequests: URLSearchParams[] = []; const historyRequests: URLSearchParams[] = []; const auditRequests: URLSearchParams[] = [];
  let failNextList = false;
  await page.route("**/api/v1/**", async (route) => {
    const requestURL = new URL(route.request().url()); const path = requestURL.pathname; const params = requestURL.searchParams;
    let body: any = { status: "ok" };
    if (path.endsWith("/readyz")) body = { status: "ready", queue_ready: true, queue_mode: "local" };
    else if (path.endsWith("/auth/me")) body = { username: "operator", role: "admin", email: "operator@example.test" };
    else if (path === "/api/v1/scenic/projects") body = [{ id: 7, name: "青山景区", project_type: "scenic", owner_username: "operator" }];
    else if (path.endsWith("/status")) body = { status: "ready" };
    else if (path.endsWith("/preview")) body = { previews: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/audit")) { auditRequests.push(params); body = { audit: [{ id: 1, action: "tourist.register", username: "operator", detail: "已记录游客授权", created_at: "2026-09-14T01:00:00Z" }], pagination: { page: Number(params.get("page") || 1), page_size: 20, total: 1, total_pages: 1 } }; }
    else if (path.endsWith("/history")) { historyRequests.push(params); body = { history: [{ id: 9, content: "游客游览匹配", created_at: "2026-09-14T01:00:00Z", result_count: 4 }], pagination: { page: Number(params.get("page") || 1), page_size: 20, total: 1, total_pages: 1 } }; }
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/camera-points")) body = { camera_points: [] };
    else if (path.endsWith("/camera-wall")) body = { camera_wall: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/dashboard")) body = { project_id: 7, today_sessions: 35 };
    else if (path === "/api/v1/scenic/projects/7/tourists") {
      listRequests.push(params);
      if (failNextList) { failNextList = false; await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "游客列表暂不可用，请重试" }) }); return; }
      const q = params.get("q") || ""; const queue = params.get("work_queue") || ""; const pageNumber = Number(params.get("page") || 1); const size = Number(params.get("page_size") || 20);
      const rows = tourists.filter((tourist) => `${tourist.ticket_code} ${tourist.visitor_label}`.includes(q) && (!queue || tourist.work_queues.includes(queue)));
      body = { tourists: rows.slice((pageNumber - 1) * size, pageNumber * size), pagination: { page: pageNumber, page_size: size, total: rows.length, total_pages: Math.ceil(rows.length / size) }, work_queue_counts: { needs_review: 17, needs_vlog: 18, failed: 0, awaiting_payment: 0, ready_delivery: 0 } };
    } else if (/\/tourists\/\d+$/.test(path)) body = tourists.find((tourist) => tourist.id === Number(path.split("/").at(-1)));
    else if (path.endsWith("/editing/catalog")) body = { enabled: false, templates: [], music: [] };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto(url); await page.getByTitle("切换到景区运营").click();
  await expect(page.getByText("共 35 条 · 第 1 / 2 页")).toBeVisible();
  await page.getByLabel("游客下一页", { exact: true }).click();
  await expect(page.getByText("共 35 条 · 第 2 / 2 页")).toBeVisible();
  await page.getByText("游客 35", { exact: true }).click();
  await expect(page.getByLabel("搜索匹配游客")).toBeVisible();
  await page.getByLabel("搜索匹配游客").fill("T-035"); await page.getByLabel("搜索匹配游客").press("Enter");
  await expect(page.getByText("共 1 条 · 第 1 / 1 页")).toBeVisible();
  await page.getByLabel("搜索匹配游客").fill("不存在的票号"); await page.getByLabel("搜索匹配游客").press("Enter");
  await expect(page.getByText("没有符合条件的游客，可调整筛选或重置。")).toBeVisible();
  await expect(page.getByText("当前操作：游客 35（不在本页，编辑仍保留）")).toBeVisible();
  await page.getByRole("button", { name: "重置筛选", exact: true }).click();
  await page.getByLabel("工作待办筛选").selectOption("needs_vlog");
  await expect(page.getByText("共 18 条 · 第 1 / 1 页")).toBeVisible();
  await page.getByRole("button", { name: "刷新景区工作区", exact: true }).click();
  await expect(page.getByLabel("工作待办筛选")).toHaveValue("needs_vlog");
  await page.getByRole("button", { name: "打开生成视频短片", exact: true }).click();
  await page.locator('.vlog-tourist-picker > summary').click();
  await expect(page.getByLabel("工作待办筛选")).toHaveValue("needs_vlog");
  const screenshotDir = process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir();
  await page.screenshot({ path: join(screenshotDir, "scenic-worklists-desktop.png"), fullPage: false });
  // A recoverable list error keeps the operator's visitor selection and supports an explicit retry.
  failNextList = true; await page.getByRole("button", { name: "查找游客", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "游客列表暂不可用" })).toBeVisible();
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "游客列表暂不可用" })).toHaveCount(0);
  await page.getByRole("button", { name: "打开游客历史", exact: true }).click();
  await page.getByLabel("检索历史关键词").fill("游客");
  await page.getByLabel("检索历史开始日期").fill("2026-09-14"); await page.getByLabel("检索历史结束日期").fill("2026-09-14");
  await page.getByRole("button", { name: "查询记录", exact: true }).click();
  await expect.poll(() => historyRequests.at(-1)?.get("q")).toBe("游客");
  expect(historyRequests.at(-1)?.get("project_id")).toBe("7");
  const historyParams = historyRequests.at(-1)!;
  expect(new Date(historyParams.get("date_to")!).getTime() - new Date(historyParams.get("date_from")!).getTime()).toBe(86400000);
  await page.getByRole("button", { name: "打开审计", exact: true }).click();
  await page.getByLabel("操作审计关键词").fill("授权"); await page.getByLabel("审计操作类型").fill("tourist.register");
  await page.getByRole("button", { name: "查询记录", exact: true }).click();
  await expect.poll(() => auditRequests.at(-1)?.get("action")).toBe("tourist.register");
  await expect(page.getByText("已记录游客授权", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.screenshot({ path: join(screenshotDir, "scenic-audit-filters-minimum-window.png"), fullPage: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  expect(await page.title()).toContain("MyReID"); expect(page.url()).toBe(url + "/");
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(listRequests.some((params) => params.get("page") === "2")).toBe(true);
  // The single 503 console message is the intentional recoverable failure above.
  expect(errors.filter((error) => !error.includes("503 (Service Unavailable)"))).toEqual([]);
});
