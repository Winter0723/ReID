import { expect, test, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { ServerResponse } from "node:http";

let server: ViteDevServer;
let streamFrames: Buffer[] = [];
let deliveredFrames = 0;
const streamConnections = new Set<ServerResponse>();
const url = "http://127.0.0.1:41735";
test.beforeAll(async () => {
  server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error",
    plugins: [{ name: "qa-camera-stream", configureServer(vite) {
      vite.middlewares.use("/qa/camera.mjpeg", (_request, response) => {
    response.writeHead(200, { "Content-Type": "multipart/x-mixed-replace; boundary=frame", "Cache-Control": "no-store" });
    streamConnections.add(response);
    let index = 0;
    const timer = setInterval(() => {
      if (!streamFrames.length || response.writableNeedDrain) return;
      const jpeg = streamFrames[index++ % streamFrames.length];
      response.write(Buffer.concat([Buffer.from(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n\r\n`), jpeg, Buffer.from("\r\n")]));
      deliveredFrames++;
    }, 1000 / 30);
    response.on("close", () => { clearInterval(timer); streamConnections.delete(response); });
      });
    } }],
    server: { host: "127.0.0.1", port: 41735, strictPort: true } });
  await server.listen();
});
test.afterAll(async () => { for (const response of streamConnections) response.destroy(); await server?.close(); });

async function setup(page: Page, scenic = false, streaming = false) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  const state = { coreRunning: false, version: 40, statusPolls: 0, starts: 0, discovered: 0, wallRunning: false, tickets: 0, snapshots: 0 };
  const points = [
    { id: 11, point_id: 11, name: "入口相机", role: "entry", camera_source: "0", is_active: true },
    { id: 12, point_id: 12, name: "湖边相机", role: "play", camera_source: "3", is_active: true },
  ];
  const wall = () => points.map((point) => ({ ...point, running: state.wallRunning,
    monitor_running: state.wallRunning || point.camera_source === "0", indexed_targets: 3,
    latest_frame_url: state.wallRunning || point.camera_source === "0" ? `/gallery/central_camera/${point.camera_source}/latest.jpg` : "",
    latest_frame_version: state.version }));
  const coreStatus = () => ({ running: state.coreRunning, monitor_running: state.coreRunning,
    camera_source: "0", indexed_targets: state.version,
    latest_frame_url: "/gallery/central_camera/0/latest.jpg", latest_frame_version: state.version,
    monitor_status: { latest_frame_url: "/gallery/central_camera/0/latest.jpg", latest_frame_version: state.version } });
  await page.addInitScript(({ scenic }) => {
    localStorage.setItem("myreid.core.access", "core-test");
    if (scenic) localStorage.setItem("myreid.scenic.access", "scenic-test");
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: scenic, livePollIntervalMs: 500 }));
  }, { scenic });
  await page.route("**/gallery/**", async (route) => {
    state.snapshots++;
    const camera = new URL(route.request().url()).pathname.split("/").at(-2);
    await route.fulfill({ contentType: "image/svg+xml", body: `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540"><rect width="960" height="540" fill="#164e63"/><text x="70" y="260" font-size="50" fill="white">CAMERA ${camera} / FRAME ${state.version}</text></svg>` });
  });
  await page.route("**/api/v1/**", async (route) => {
    const request = new URL(route.request().url());
    const path = request.pathname;
    let body: unknown = { status: "ok" };
    if (path.endsWith("/readyz")) body = { status: "ready", queue_mode: "local", queue_ready: true };
    else if (streaming && path.endsWith("/preview-stream")) { state.tickets++; body = { url: `/qa/camera.mjpeg?ticket=${state.tickets}`, expires_in: 300 }; }
    else if (path.endsWith("/auth/me")) body = { username: "operator", role: "operator" };
    else if (path === "/api/v1/projects") body = [{ id: 7, name: "实时相机验证", project_type: "camera", camera_source: "0", owner_username: "operator" }];
    else if (path === "/api/v1/scenic/projects") body = [{ id: 8, name: "景区相机验证", project_type: "scenic", owner_username: "operator" }];
    else if (path.endsWith("/camera/start")) { state.coreRunning = true; body = coreStatus(); }
    else if (path.endsWith("/camera/stop")) { state.coreRunning = false; body = coreStatus(); }
    else if (path.endsWith("/camera/status")) { state.statusPolls++; if (state.coreRunning && request.searchParams.get("light") === "true") state.version++; body = coreStatus(); }
    else if (path.endsWith("/camera-points/discover")) { state.discovered++; body = { status: "ok", created: 0, camera_points: points }; }
    else if (path.endsWith("/camera-points/start-all")) { state.starts++; state.wallRunning = true; body = { camera_wall: wall() }; }
    else if (path.endsWith("/camera-points/stop-all")) { state.wallRunning = false; body = { camera_wall: wall() }; }
    else if (/camera-points\/\d+\/preview$/.test(path)) body = { camera: wall().find((point) => point.id === Number(path.split("/").at(-2))) };
    else if (path.endsWith("/camera-points")) body = { camera_points: points };
    else if (path.endsWith("/camera-wall")) { state.version++; body = { camera_wall: wall() }; }
    else if (path.endsWith("/cameras")) body = { devices: points.map((point) => ({ source: point.camera_source, label: point.name })) };
    else if (path.endsWith("/status")) body = { status: "ready", indexed_targets: state.version };
    else if (path.endsWith("/preview")) body = { previews: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/history")) body = { history: [] };
    else if (path.endsWith("/audit")) body = { audit: [] };
    else if (path.endsWith("/tourists")) body = { tourists: [] };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  return { state, errors };
}

async function health(page: Page, errors: string[]) {
  expect(page.url()).toBe(`${url}/`);
  await expect(page).toHaveTitle(/MyReID/);
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
}

test("creating a project closes management and selects the new workspace", async ({ page }) => {
  const { errors } = await setup(page);
  let created = false;
  await page.route("**/api/v1/projects", async route => {
    const project = { id: 19, name: "新建项目验证", project_type: "media", owner_username: "operator" };
    if (route.request().method() === "POST") {
      created = true;
      await route.fulfill({ json: project });
    } else if (created) {
      await route.fulfill({ json: [project] });
    } else {
      await route.fallback();
    }
  });
  await page.goto(url);
  await expect(page.locator(".workspace-topbar")).toContainText("实时相机验证");
  await page.getByRole("button", { name: /^打开项目管理：/ }).click();
  await page.getByPlaceholder("项目名称").fill("新建项目验证");
  await page.getByRole("button", { name: "确认创建", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".workspace-topbar")).toContainText("新建项目验证");
  await health(page, errors);
});

test("core preview keeps advancing after stop/start with activity refresh disabled", async ({ page }) => {
  const { state, errors } = await setup(page);
  await page.goto(url);
  const preview = page.getByAltText("摄像头实时预览");
  await expect(preview).toBeVisible();
  await expect(preview).toHaveAttribute("src", /v=40$/);
  await page.getByRole("button", { name: "启用实时入库", exact: true }).click();
  await expect.poll(() => state.version).toBeGreaterThan(41);
  await expect(preview).not.toHaveAttribute("src", /v=40$/);
  await page.getByRole("button", { name: "暂停实时入库", exact: true }).click();
  await expect(page.getByRole("button", { name: "启用实时入库", exact: true })).toBeVisible();
  const stopped = await preview.getAttribute("src");
  await page.getByRole("button", { name: "启用实时入库", exact: true }).click();
  await expect(preview).not.toHaveAttribute("src", stopped!);
  const restarted = await preview.getAttribute("src");
  await expect(preview).not.toHaveAttribute("src", restarted!);
  await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true);
  await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "core-camera-live-restart.png") });
  await health(page, errors);
});

test("both camera surfaces render continuous MJPEG without reopening on status polls", async ({ page }) => {
  const { state, errors } = await setup(page, true, true);
  // Real multipart HTTP, synthetic JPEGs: validates browser decoding and
  // lifecycle independently of physical camera and inference availability.
  streamFrames = (await page.evaluate(() => ["#185ee0", "#e05318"].map(color => {
    const canvas = document.createElement("canvas");
    canvas.width = 640; canvas.height = 360;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = color; ctx.fillRect(0, 0, 640, 360);
    ctx.fillStyle = "white"; ctx.font = "28px sans-serif";
    ctx.fillText("640 x 360 / continuous 30 FPS", 45, 180);
    return canvas.toDataURL("image/jpeg").split(",")[1];
  }))).map(value => Buffer.from(value, "base64"));
  await page.goto(url);
  const preview = page.locator("img.stream");
  await expect(preview).toHaveCount(1);
  await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(640);
  const source = await preview.getAttribute("src");
  const polls = state.statusPolls;
  await expect.poll(() => state.statusPolls).toBeGreaterThan(polls + 1);
  expect(await preview.getAttribute("src")).toBe(source);
  expect(state.tickets).toBe(1);
  state.coreRunning = true;
  const snapshots = state.snapshots;
  const streamingPolls = state.statusPolls;
  await expect.poll(() => state.statusPolls).toBeGreaterThan(streamingPolls + 2);
  expect(state.snapshots).toBe(snapshots);
  const colors = await preview.evaluate(async (image: HTMLImageElement) => {
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const seen = new Set<string>();
    for (let i = 0; i < 30; i++) {
      await new Promise(resolve => requestAnimationFrame(resolve));
      ctx.drawImage(image, 0, 0, 1, 1);
      seen.add(Array.from(ctx.getImageData(0, 0, 1, 1).data).join(","));
    }
    return seen.size;
  });
  expect(colors).toBeGreaterThan(1);
  await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "core-camera-continuous.png") });
  await page.getByRole("button", { name: "打开检索结果", exact: true }).click();
  await expect(page.locator(".core-result-panel img.stream")).toHaveCount(1);
  await expect.poll(() => page.locator(".core-result-panel img.stream").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(640);
  await expect(page.getByRole("heading", { name: "检索结果", exact: true })).toBeVisible();
  await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "search-camera-continuous.png") });
  await page.getByTitle("切换到景区运营").click();
  await page.getByRole("button", { name: "打开运营总览", exact: true }).click();
  await expect(page.locator(".overview-camera-frame img.stream")).toHaveCount(1);
  await expect.poll(() => page.locator(".overview-camera-frame img.stream").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(640);
  await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "overview-camera-continuous.png") });
  await page.getByRole("button", { name: "打开园内摄像头", exact: true }).click();
  await expect(page.locator(".camera-grid img.stream")).toHaveCount(2);
  await expect.poll(() => streamConnections.size).toBe(2);
  const sent = deliveredFrames;
  await expect.poll(() => deliveredFrames).toBeGreaterThan(sent + 10);
  await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "scenic-camera-continuous.png") });
  await health(page, errors);
  await page.goto("about:blank");
  await expect.poll(() => streamConnections.size).toBe(0);
});

test("core preview retains its decoded frame while the next image is loading", async ({ page }) => {
  const { state, errors } = await setup(page);
  let releaseFrame!: () => void;
  let frameRequested!: () => void;
  let nextSource = "";
  const heldFrame = new Promise<void>((resolve) => { releaseFrame = resolve; });
  const requested = new Promise<void>((resolve) => { frameRequested = resolve; });
  await page.route("**/gallery/**", async (route) => {
    if (new URL(route.request().url()).searchParams.get("v") !== "40") {
      // Hold the next image across multiple 500 ms polls, keeping its version
      // stable so the browser can finish it instead of abandoning slow frames.
      state.coreRunning = false;
      nextSource = route.request().url();
      frameRequested();
      await heldFrame;
    }
    await route.fallback();
  });
  try {
    await page.goto(url);
    const preview = page.getByAltText("摄像头实时预览");
    await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true);
    const originalNode = await preview.elementHandle();
    const originalSource = await preview.evaluate((image: HTMLImageElement) => image.currentSrc);
    await page.getByRole("button", { name: "启用实时入库", exact: true }).click();
    await requested;
    const pollsAtRequest = state.statusPolls;
    await expect.poll(() => state.statusPolls).toBeGreaterThanOrEqual(pollsAtRequest + 2);
    expect(nextSource).not.toBe(originalSource);
    expect(new URL((await preview.getAttribute("src"))!, page.url()).href).toBe(originalSource);
    expect(await originalNode!.evaluate((image) => image.isConnected)).toBe(true);
    expect(await preview.evaluate((image: HTMLImageElement) => ({ currentSrc: image.currentSrc, width: image.naturalWidth, complete: image.complete })))
      .toEqual({ currentSrc: originalSource, width: 960, complete: true });
    await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "core-camera-pending-frame.png") });
    releaseFrame();
    await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.currentSrc)).toBe(nextSource);
    await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.complete)).toBe(true);
    expect(await originalNode!.evaluate((image) => image.isConnected)).toBe(true);
    await health(page, errors);
  } finally {
    releaseFrame();
  }
});

test("entering scenic starts all cameras and ordinary refresh respects stop", async ({ page }) => {
  const { state, errors } = await setup(page, true);
  await page.goto(url);
  await page.getByTitle("切换到景区运营").click();
  await expect(page.locator("main.scenic-shell")).toBeVisible();
  await expect.poll(() => state.starts).toBe(1);
  // Entering the workspace starts configured cameras; hardware discovery is explicit.
  expect(state.discovered).toBe(0);
  await page.getByLabel("入口抓拍相机").selectOption("12");
  await expect(page.getByRole("button", { name: "抓拍照片", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "打开园内摄像头", exact: true }).click();
  await expect(page.locator(".camera-grid .camera-card")).toHaveCount(2);
  await expect(page.locator(".camera-grid .capture-pill.on")).toHaveCount(2);
  await expect(page.getByAltText("摄像头实时画面")).toHaveCount(2);
  await page.screenshot({ path: join(process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir(), "scenic-cameras-auto-start.png") });
  await page.getByRole("button", { name: "停止监控墙", exact: true }).click();
  await expect(page.locator(".camera-grid .capture-pill.on")).toHaveCount(0);
  await page.getByRole("button", { name: "刷新景区工作区", exact: true }).click();
  expect(state.starts).toBe(1);
  await page.getByTitle("返回基础系统").click();
  await page.getByTitle("切换到景区运营").click();
  await expect.poll(() => state.starts).toBe(2);
  await health(page, errors);
});
