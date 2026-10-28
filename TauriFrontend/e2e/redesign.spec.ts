import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Deliberately deterministic API data: real model/render/payment contracts are
// exercised in the backend suite, while this checks the redesigned UI wiring.
let server: ViteDevServer;
const url = "http://127.0.0.1:41738";
const pixel = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1sAAAAASUVORK5CYII=";
test.beforeAll(async () => { server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error", server: { host: "127.0.0.1", port: 41738, strictPort: true } }); await server.listen(); });
test.afterAll(async () => { await server?.close(); });

test("reference layout keeps registration, review, overview and search connected", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    for (const scope of ["core", "scenic"]) { localStorage.setItem(`myreid.${scope}.access`, "fixture"); localStorage.setItem(`myreid.${scope}.refresh`, "fixture"); }
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: false }));
  });
  const segments = Array.from({ length: 4 }, (_, index) => ({ id: index + 1, camera_name: `游览点位 ${index + 1}`, timestamp: "2026-10-04 10:30", similarity: .96 - index * .02, start_seconds: 0, end_seconds: 12, included: true, thumbnail_url: pixel }));
  const tourist = { id: 1, ticket_code: "QL20261004001", visitor_label: "测试游客", status: "registered", created_at: "2026-10-04 09:42", entry_image_url: pixel, segment_count: 4, segments, vlog_jobs: [], orders: [] };
  let registered = false;
  let registrationBody = "";
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: unknown = { status: "ok" };
    if (path.endsWith("/readyz")) body = { status: "ready", queue_ready: true, queue_mode: "local" };
    else if (path.endsWith("/auth/me")) body = { username: "operator", role: "admin" };
    else if (path.endsWith("/projects")) body = [{ id: 7, name: "青山景区", reid_model: { id: "clip", name: "CLIP-ReID", available: true, capabilities: { image: true, text: true } }, project_type: path.includes("scenic") ? "scenic" : "media" }];
    else if (path.endsWith("/status")) body = { indexed_targets: 4 };
    else if (path.endsWith("/preview")) body = { previews: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/history")) body = { history: [] };
    else if (path.endsWith("/audit")) body = { audit: [] };
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/camera-points")) body = { camera_points: [] };
    else if (path.endsWith("/camera-wall")) body = { camera_wall: [] };
    else if (path.endsWith("/dashboard")) body = { status: "ok", project_id: 7, today_sessions: registered ? 1 : 0, matched_sessions: tourist.status === "matched" ? 1 : 0, recent_sessions: registered ? [tourist] : [] };
    else if (path.endsWith("/tourists") && method === "POST") { registrationBody = route.request().postData() || ""; registered = true; body = tourist; }
    else if (path.endsWith("/tourists")) body = { tourists: registered ? [tourist] : [] };
    else if (path.endsWith("/tourists/1")) body = tourist;
    else if (path.endsWith("/match")) { tourist.status = "matched"; body = { status: "ok", tourist, segments }; }
    else if (/\/segments\/\d+$/.test(path)) { const segment = segments[Number(path.split("/").at(-1)) - 1]; Object.assign(segment, route.request().postDataJSON()); body = segment; }
    else if (path.endsWith("/editing/catalog")) body = { enabled: false, templates: [], music: [] };
    else if (path.endsWith("/search/text")) body = { task_id: 1, results: segments.map(segment => ({ id: segment.id, rank: segment.id, similarity: segment.similarity, thumbnail_url: pixel, media_filename: segment.camera_name })) };
    await route.fulfill({ json: body });
  });
  await page.goto(url);
  await page.getByTitle("切换到景区运营").click();
  await page.getByRole("button", { name: "上传照片", exact: true }).click();
  await page.getByLabel("选择游客照片", { exact: true }).setInputFiles({ name: "visitor.png", mimeType: "image/png", buffer: Buffer.from(pixel.split(",")[1], "base64") });
  await page.getByLabel("门票号", { exact: true }).fill(tourist.ticket_code);
  await page.getByLabel("游客备注", { exact: true }).fill(tourist.visitor_label);
  await expect(page.getByRole("button", { name: "提交登记", exact: true })).toBeDisabled();
  await page.getByLabel("已获得游客授权", { exact: true }).check();
  await page.getByRole("button", { name: "提交登记", exact: true }).click();
  await expect.poll(() => registered).toBe(true);
  expect(registrationBody).toContain(tourist.ticket_code);
  expect(registrationBody).toContain('name="consent_granted"');
  await page.getByText("测试游客", { exact: true }).click();
  await page.getByRole("button", { name: "开始匹配", exact: true }).click();
  await expect(page.locator(".segment-row")).toHaveCount(4);
  await page.locator(".segment-row").first().getByRole("checkbox").uncheck();
  await expect(page.locator(".review-count strong")).toHaveText("3 个精彩片段");
  await page.getByRole("button", { name: "确认并制作短片 →", exact: true }).click();
  await expect(page.locator(".vlog-tourist-picker > summary")).toContainText("测试游客");
  await page.getByRole("button", { name: "打开运营总览", exact: true }).click();
  await expect(page.locator(".overview-metrics article").first().locator("strong")).toHaveText("1");
  await page.locator(".overview-recent").getByRole("button", { name: "查看记录 →", exact: true }).click();
  await expect(page.locator(".review-count strong")).toHaveText("3 个精彩片段");
  await page.getByTitle("返回基础系统").click();
  await page.getByRole("button", { name: "打开检索结果", exact: true }).click();
  await page.getByPlaceholder("描述目标人物的衣着、外貌等特征").fill("穿浅色外套的游客");
  await page.getByRole("button", { name: "以文搜图", exact: true }).click();
  await expect(page.locator(".result-tile")).toHaveCount(4);
  await page.locator(".result-tile").first().click();
  await expect(page.locator(".result-detail")).toBeVisible();
  for (const width of [1440, 1120, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), `redesign-search-${width}.png`) });
  }
  await expect(page).toHaveTitle(/MyReID/);
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
});


test("visitor staff confirmation requires explicit review", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await page.addInitScript(() => {
    for (const scope of ["core", "scenic"]) { localStorage.setItem(`myreid.${scope}.access`, "fixture"); localStorage.setItem(`myreid.${scope}.refresh`, "fixture"); }
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: false }));
  });
  let confirmations = 0, verifications = 0;
  const row = { id: 7, session_id: 9, stage: "ready", message: "视频已就绪", reason: "", error: "", vlog_id: 2,
    identity_verified: false,
    order: { id: 3, order_no: "MANUAL-001", amount_cents: 1990, status: "pending", payment_provider: "visitor_manual" } };
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = { status: "ok" };
    if (path.endsWith("/auth/me")) body = { username: "operator", role: "operator" };
    else if (path.endsWith("/projects")) body = [{ id: 7, name: "青山景区", reid_model: { id: "clip", name: "CLIP-ReID", available: true, capabilities: { image: true, text: true } }, project_type: path.includes("scenic") ? "scenic" : "media" }];
    else if (path.endsWith("/visitor-service")) body = { enabled: true, description: "游客服务", consent_text: "授权", consent_version: "v1", price_cents: 1990, entry_url: "http://192.168.1.100:5174/visitor/#/sites/7" };
    else if (path.endsWith("/visitor-queue")) body = { requests: [row], can_confirm_payment: true };
    else if (path.endsWith("/visitor-requests/7/resume")) {
      expect(route.request().postDataJSON()).toEqual({ action: "verify", vlog_id: null });
      verifications++; row.identity_verified = true; body = row;
    }
    else if (path.endsWith("/orders/3/mark-paid")) { confirmations++; row.order.status = "paid"; body = row.order; }
    else if (path.endsWith("/tourists")) body = { tourists: [] };
    else if (path.endsWith("/camera-points")) body = { camera_points: [] };
    else if (path.endsWith("/camera-wall")) body = { camera_wall: [] };
    else if (path.endsWith("/dashboard")) body = { project_id: 7, recent_sessions: [] };
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/history")) body = { history: [] };
    else if (path.endsWith("/audit")) body = { audit: [] };
    else if (path.endsWith("/editing/catalog")) body = { enabled: false, templates: [], music: [] };
    await route.fulfill({ json: body });
  });
  await page.goto(url);
  await page.getByTitle("切换到景区运营").click();
  await page.getByRole("button", { name: "打开游客自助服务", exact: true }).click();
  const verification = page.getByRole("button", { name: "已现场核验本人、账号、票据与照片", exact: true });
  await expect(verification).toBeVisible();
  await verification.scrollIntoViewIfNeeded();
  if (process.env.MYREID_QA_DIR) await page.screenshot({ path: `${process.env.MYREID_QA_DIR}/identity-verification.png`, fullPage: true });
  await verification.click();
  await expect(verification).toHaveCount(0);
  expect(verifications).toBe(1);
  await page.getByRole("button", { name: "确认游客付款", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "确认游客付款" });
  await expect(dialog).toContainText("MANUAL-001");
  await expect(dialog).toContainText("¥19.90");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect(confirmations).toBe(0);
  await page.getByRole("button", { name: "确认游客付款", exact: true }).click();
  if (process.env.MYREID_QA_DIR) await page.screenshot({ path: `${process.env.MYREID_QA_DIR}/manual-staff-confirm.png`, fullPage: true });
  await dialog.getByRole("button", { name: "确认，开放下载" }).click();
  await expect(page.getByText("付款已确认，游客刷新后即可下载领取。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认游客付款", exact: true })).toHaveCount(0);
  expect(confirmations).toBe(1);
  await expect(page).toHaveTitle(/MyReID/);
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
});
