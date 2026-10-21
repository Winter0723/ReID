import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { join } from "node:path";
import { tmpdir } from "node:os";
let server: ViteDevServer;
const url = "http://127.0.0.1:41736";
test.beforeAll(async () => {
  server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error",
    server: { host: "127.0.0.1", port: 41736, strictPort: true } });
  await server.listen();
});
test.afterAll(async () => { await server?.close(); });

test("target selection, synchronized stream, controls and navigation cleanup", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", e => { if (["error", "warning"].includes(e.type())) errors.push(e.text()); });
  await page.addInitScript(() => localStorage.setItem("myreid.core.access", "test-token"));
  let stopped = 0, started = 0;
  const controls: string[] = [];
  let previewRequested = false;
  let releasePreview: () => void = () => {};
  const previewGate = new Promise<void>(resolve => { releasePreview = resolve; });
  const preview = 'data:image/svg+xml;base64,' + Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><rect width="300" height="200" fill="#334155"/><rect x="30" y="20" width="80" height="170" rx="30" fill="#38bdf8"/><rect x="180" y="20" width="80" height="170" rx="30" fill="#94a3b8"/></svg>').toString('base64');
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = { status: "ok" };
    if (path.endsWith('/readyz')) body = { status: "ready", queue_mode: "local", queue_ready: true };
    else if (path.endsWith('/auth/me')) body = { username: "operator", role: "operator" };
    else if (path === '/api/v1/projects') body = [{ id: 7, name: "实时识别验证", project_type: "normal", owner_username: "operator" }];
    else if (path.endsWith('/realtime/media/11/preview')) { previewRequested = true; await previewGate; body = { preview }; }
    else if (path.endsWith('/realtime/inputs')) body = { id: 'reference', kind: 'image', width: 300, height: 200, preview, boxes: [{ box: [.1,.1,.37,.95], confidence: .9 }, { box: [.6,.1,.87,.95], confidence: .8 }] };
    else if (path.endsWith('/realtime/sessions')) { started++; expect(route.request().postDataJSON().source).toEqual({ media_id: 11 }); body = { id: 'session', status: 'running' }; }
    else if (path.endsWith('/stream-ticket')) body = { ticket: 'single-use' };
    else if (path.endsWith('/control')) { const action = route.request().postDataJSON().action; controls.push(action); if (action === 'stop') stopped++; body = { id: 'session', status: action === 'pause' ? 'paused' : action === 'stop' ? 'stopped' : 'running' }; }
    else if (path.endsWith('/media')) body = { media: [{ id: 11, filename: '行人演示.mp4', media_type: 'video' }] };
    else if (path.endsWith('/cameras')) body = { devices: [{ source: '0', label: '后端 USB 0' }, { source: 'phone', label: 'Iriun Webcam' }, { source: 'entrance', label: '入口网络相机' }] };
    else if (path.endsWith('/status')) body = { status: 'ready', indexed_targets: 0 };
    else if (path.endsWith('/preview')) body = { previews: [] };
    else if (path.endsWith('/jobs')) body = { jobs: [] };
    else if (path.endsWith('/history')) body = { history: [] };
    else if (path.endsWith('/audit')) body = { audit: [] };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  let acks = 0;
  let interruptCamera = () => {};
  let recoverCamera = () => {};
  await page.routeWebSocket('**/realtime/sessions/*/stream?*', ws => {
    const frame = { type: 'frame', session_id: 'session', sequence: 1, source_time: 1.2, width: 1, height: 1,
      target_state: 'confirmed', fps: 10, processing_ms: 50, sent_at: Date.now()/1000,
      jpeg: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7xkAAAAASUVORK5CYII=',
      people: [{ track_id: 1, target: true, similarity: .85, detection_confidence: .93 }] };
    ws.send(JSON.stringify(frame));
    interruptCamera = () => ws.send(JSON.stringify({ type: 'status', status: 'running', epoch: 0,
      error: '相机暂未输出新画面，等待重新连接' }));
    recoverCamera = () => ws.send(JSON.stringify({ ...frame, sequence: 2 }));
    ws.onMessage(message => { if (JSON.parse(String(message)).ack) acks++; });
  });
  await page.goto(url);
  await page.getByRole('button', { name: '实时行人识别', exact: true }).click();
  await expect(page.getByRole('heading', { name: '实时行人识别' })).toBeVisible();
  await expect(page.getByRole('button', { name: '开始识别' })).toBeDisabled();
  await page.getByLabel('上传目标照片').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: Buffer.from('mock-upload') });
  await page.getByRole('button', { name: '选择人物 1' }).click();
  await expect(page.getByAltText('选定目标裁剪预览')).toBeVisible();
  await page.getByLabel('上传识别视频').setInputFiles({ name: 'test.mp4', mimeType: 'video/mp4', buffer: Buffer.from('mock-video') });
  await expect(page.getByAltText('所选视频首帧预览')).toBeVisible();
  await page.getByLabel('视频来源', { exact: true }).selectOption('camera');
  await expect(page.getByLabel('已配置摄像头').locator('option')).toHaveCount(3);
  await page.getByLabel('已配置摄像头').selectOption('phone');
  await expect(page.getByLabel('摄像头地址')).toHaveValue('phone');
  await page.getByLabel('已配置摄像头').selectOption('entrance');
  await expect(page.getByLabel('摄像头地址')).toHaveValue('entrance');
  await expect(page.getByAltText('所选视频首帧预览')).toHaveCount(0);
  await page.getByLabel('摄像头地址').fill('rtsp://127.0.0.1/test');
  await expect(page.getByText('请选择连接到服务器的 USB 摄像头', { exact: false })).toBeVisible();
  await page.getByLabel('视频来源', { exact: true }).selectOption('media');
  await page.getByLabel('项目视频', { exact: true }).selectOption('11');
  await expect.poll(() => previewRequested).toBe(true);
  await page.getByLabel('视频来源', { exact: true }).selectOption('camera');
  releasePreview();
  await expect(page.getByAltText('所选视频首帧预览')).toHaveCount(0);
  await page.getByLabel('视频来源', { exact: true }).selectOption('media');
  await expect(page.getByAltText('所选视频首帧预览')).toBeVisible();
  await page.screenshot({ path: join(tmpdir(), 'myreid-realtime-ready.png') });
  await page.getByRole('button', { name: '开始识别' }).click();
  await expect(page.getByAltText('实时行人识别画面', { exact: true })).toBeVisible();
  await expect(page.getByText('已确认', { exact: true })).toBeVisible();
  await expect.poll(() => acks).toBe(1);
  interruptCamera();
  await expect(page.getByText('相机暂未输出新画面，等待重新连接', { exact: true })).toBeVisible();
  await expect(page.getByAltText('实时行人识别画面', { exact: true })).toHaveCount(0);
  recoverCamera();
  await expect(page.getByAltText('实时行人识别画面', { exact: true })).toBeVisible();
  await expect(page.getByText('相机暂未输出新画面，等待重新连接', { exact: true })).toHaveCount(0);
  await expect.poll(() => acks).toBe(2);
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await expect(page.getByRole('button', { name: '继续', exact: true })).toBeVisible();
  await expect(page.getByAltText('实时行人识别画面', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '继续', exact: true }).click();
  await expect(page.getByRole('button', { name: '暂停', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '实时行人识别', exact: true }).click();
  await expect.poll(() => stopped).toBe(1);
  expect(started).toBe(1); expect(controls).toEqual(['pause','resume','stop']);
  expect(errors).toEqual([]);
  expect(await page.locator('vite-error-overlay').count()).toBe(0);
  expect(await page.title()).toContain('MyReID');
});
