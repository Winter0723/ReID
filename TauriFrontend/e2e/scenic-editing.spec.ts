import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { DraftRecord, EditingCatalog } from "../src/lib/api/scenicEditing";

// UI acceptance uses a deterministic API fixture. Backend rendering is covered by real FFmpeg tests.
let server: ViteDevServer;
const url = "http://127.0.0.1:41740";
const poster = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#adcbb6"/><path d="M0 250L140 80L290 230L450 55L640 230V360H0" fill="#527d67"/><path d="M0 290Q300 220 640 300V360H0" fill="#609f9d"/><circle cx="325" cy="155" r="22" fill="#f2ceb0"/><path d="M292 270V195Q323 171 355 195V270" fill="#f7e7ce"/><path d="M305 270L298 335M340 270L350 335" stroke="#334952" stroke-width="17"/></svg>');
const catalog: EditingCatalog = { enabled: true, tracking_enabled: true, templates: [
  { id: "documentary", name: "旅行纪实", version: "1", description: "沿游览时间展开", music_id: "scenic_morning", min_clip_seconds: 3.5, max_clip_seconds: 7, color: "#79B6A6", transition_seconds: .25 },
  { id: "upbeat", name: "轻快打卡", version: "1", description: "轻快节奏", music_id: "scenic_footsteps", min_clip_seconds: 1.5, max_clip_seconds: 3.8, color: "#F0BE62" },
], music: [{ id: "scenic_morning", name: "山间晨光" }, { id: "scenic_footsteps", name: "轻快脚步" }] };
function draftFixture(id: number): DraftRecord {
  const clips = ["湖畔栈道", "山间花园", "观景平台"].map((name, index) => ({ id: `clip-${index}`, segment_id: index + 1, media_id: index + 1, object_key: `photo-${index}.jpg`, source_sha256: `hash-${index}`, kind: "image" as const, start_seconds: 0, end_seconds: 5, camera_name: name, thumbnail_url: poster, locked: false, included: true, keep_audio: false, focus_x: .5, focus_y: .5, crop_mode: "fit" as const }));
  return { id, session_id: 1, revision: 1, data: { schema_version: 1, template_id: "documentary", template_version: "1", title: "我的游览记忆", brand: "青山景区", music_id: "scenic_morning", music_volume: .25, original_volume: 1, landscape_seconds: 75, portrait_seconds: 45, sources: clips, variants: { landscape: structuredClone(clips), portrait: structuredClone(clips) }, warnings: ["可用素材不足，生成精简回忆，不重复镜头填充。"] } };
}
test.beforeAll(async () => {
  server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error", server: { host: "127.0.0.1", port: 41740, strictPort: true } });
  await server.listen();
});
test.afterAll(async () => { await server?.close(); });

test("scenic editing preserves drafts, submits frozen revisions and binds explicit delivery version", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem("myreid.scenic.access", "fixture-token");
    localStorage.setItem("myreid.scenic.refresh", "fixture-refresh");
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: false }));
  });
  let draft: DraftRecord | null = null;
  let drafts: DraftRecord[] = [];
  let submitted: Record<string, unknown> | null = null;
  let ordered: Record<string, unknown> | null = null;
  const tourists: any[] = [{ id: 1, ticket_code: "T-001", visitor_label: "林女士", status: "matched", entry_image_url: poster, segment_count: 3, segments: [{ id: 1, included: true }], vlog_jobs: [], orders: [] }, { id: 2, ticket_code: "T-002", visitor_label: "陈先生", status: "registered", entry_image_url: poster, segments: [], vlog_jobs: [], orders: [] }];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: any = { status: "ok" };
    if (path.endsWith("/readyz")) body = { status: "ready", queue_ready: true, queue_mode: "local" };
    else if (path.endsWith("/auth/me")) body = { username: "editor", role: "admin", email: "editor@example.test" };
    else if (path === "/api/v1/scenic/projects") body = [{ id: 7, name: "青山景区", project_type: "scenic", owner_username: "editor" }];
    else if (path.endsWith("/status")) body = { status: "ready", indexed_targets: 3 };
    else if (path.endsWith("/preview")) body = { previews: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/audit")) body = { audit: [] };
    else if (path.endsWith("/history")) body = { history: [] };
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/camera-points")) body = { camera_points: [] };
    else if (path.endsWith("/camera-wall")) body = { camera_wall: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/dashboard")) body = { project_id: 7, today_sessions: 2, matched_sessions: 1, ready_vlogs: 0 };
    else if (path === "/api/v1/scenic/projects/7/tourists") body = { tourists };
    else if (/\/tourists\/[12]$/.test(path)) body = tourists[Number(path.at(-1)) - 1];
    else if (path.endsWith("/editing/catalog")) body = catalog;
    else if (path.endsWith("/drafts") && method === "GET") body = { drafts: path.includes("/tourists/1/") ? drafts : [] };
    else if (path.endsWith("/drafts/auto")) {
      const options = route.request().postDataJSON();
      draft = draftFixture((draft?.id || 0) + 1);
      Object.assign(draft.data, options);
      const previous = drafts.find((item) => item.id === options.previous_draft_id);
      if (previous) for (const orientation of ["landscape", "portrait"] as const) {
        for (const clip of draft.data.variants[orientation]) {
          if (previous.data.variants[orientation].some((old) => old.id === clip.id && !old.included)) clip.included = false;
        }
      }
      drafts.unshift(structuredClone(draft));
      body = draft;
    } else if (/\/drafts\/\d+$/.test(path) && method === "PUT") {
      const payload = route.request().postDataJSON();
      draft = { ...draft!, data: payload.data, revision: payload.revision + 1 };
      drafts = drafts.map((item) => item.id === draft!.id ? structuredClone(draft!) : item);
      body = draft;
    } else if (path.endsWith("/vlog")) {
      submitted = route.request().postDataJSON();
      const vlog = { id: 44, status: "succeeded", draft_id: draft!.id, draft_revision: draft!.revision, variants: ["landscape", "portrait"].map((orientation, index) => ({ id: 100 + index, orientation, status: "succeeded", duration_seconds: 15, cover_url: poster, preview_url: `/api/v1/scenic/vlogs/44/variants/${index}/preview`, full_url: `/api/v1/scenic/vlogs/44/variants/${index}/full`, draft_preview_url: `/api/v1/scenic/vlogs/44/variants/${index}/draft-preview` })) };
      tourists[0].vlog_jobs = [vlog];
      body = { status: "queued", job_id: "fixture-job", vlog };
    } else if (path.endsWith("/orders")) {
      ordered = route.request().postDataJSON();
      body = { id: 8, status: "pending", order_no: "QS-20260908", vlog_job_id: 44, amount_cents: 1000, delivery_page_url: "/api/v1/scenic/deliveries/demo/page" };
      tourists[0].orders = [body];
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto(url);
  await page.getByTitle("切换到景区运营").click();
  await expect(page.getByText("林女士", { exact: true })).toBeVisible();
  await page.getByText("林女士", { exact: true }).click();
  await page.getByRole("button", { name: "打开生成视频短片", exact: true }).click();
  await expect(page.getByRole("region", { name: "Vlog 剪辑工作台" })).toBeVisible();
  await page.getByRole("button", { name: "生成自动草稿", exact: true }).click();
  await expect(page.getByLabel("影片标题")).toHaveValue("我的游览记忆");
  await page.getByLabel("影片标题").fill("青山一日 · 湖畔的风");
  await page.getByLabel("后移镜头 1").click();
  await expect(page.getByLabel("编辑镜头 1")).toContainText("山间花园");
  await page.getByLabel("包含镜头 1", { exact: true }).uncheck();
  await expect(page.getByLabel("编辑镜头 1")).toContainText("已排除");
  await page.getByRole("button", { name: "竖版 9:16", exact: true }).click();
  await expect(page.getByLabel("编辑镜头 1")).toContainText("湖畔栈道");
  await expect(page.getByLabel("包含镜头 2", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("编辑镜头 2")).toContainText("已排除");
  await expect(page.locator(".timeline-summary")).toContainText("2 个镜头");
  await page.getByLabel("编辑镜头 1", { exact: true }).click();
  await page.getByLabel("构图方式").selectOption("focus");
  await page.getByRole("button", { name: "在原图选择焦点" }).click({ position: { x: 50, y: 30 } });
  await page.getByRole("button", { name: "横版 16:9", exact: true }).click();
  await page.getByRole("button", { name: /^(比较草稿|收起比较)$/ }).click();
  const comparison = page.getByRole("region", { name: "草稿版本比较", exact: true });
  await expect(comparison).toBeVisible();
  await expect(comparison).toContainText("影片标题");
  await expect(comparison).toContainText("青山一日 · 湖畔的风");
  for (const name of ["横版比较", "竖版比较"]) {
    const variant = comparison.getByRole("article", { name, exact: true });
    await expect(variant).toContainText("3 个镜头");
    await expect(variant).toContainText("2 个镜头");
    await expect(variant).toContainText("入片选择");
  }
  expect(drafts[0].data.title).toBe("我的游览记忆");
  expect(drafts[0].data.variants.landscape.every((item) => item.included)).toBe(true);
  const screenshotDir = process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir();
  await comparison.scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(screenshotDir, "scenic-draft-comparison-desktop.png"), fullPage: false });
  await page.locator('.vlog-tourist-picker > summary').click();
  await page.getByText("陈先生", { exact: true }).click();
  await expect(page.getByRole("button", { name: "生成自动草稿", exact: true })).toBeVisible();
  await expect(comparison).toHaveCount(0);
  await page.getByText("林女士", { exact: true }).first().click();
  await expect(page.getByLabel("影片标题")).toHaveValue("青山一日 · 湖畔的风");
  await expect(comparison).toHaveCount(0);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("草稿已保存，提交后该版本会被冻结。")).toBeVisible();
  await page.getByRole("button", { name: /^(比较草稿|收起比较)$/ }).click();
  await expect(comparison).toContainText("当前草稿与所选草稿的编辑内容一致");
  await page.getByRole("button", { name: /^(比较草稿|收起比较)$/ }).click();
  await page.getByRole("region", { name: "Vlog 剪辑工作台" }).evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: join(screenshotDir, "scenic-vlog-editor-desktop.png"), fullPage: false });
  await page.getByRole("button", { name: "生成横竖双版", exact: true }).click();
  await expect(page.getByText("成片版本 #44", { exact: true })).toBeVisible();
  expect(submitted).toEqual({ draft_id: 1, revision: 2 });
  for (const clips of Object.values(draft!.data.variants)) {
    expect(clips.filter((clip) => clip.included).map((clip) => clip.id)).toEqual(["clip-0", "clip-2"]);
  }
  await page.getByLabel("交付成片版本").selectOption("44");
  await page.locator(".payment-controls input[type=number]").fill("1000");
  await page.getByRole("button", { name: "创建订单", exact: true }).click();
  await expect(page.getByText("QS-20260908", { exact: false })).toBeVisible();
  expect(ordered).toMatchObject({ vlog_job_id: 44, amount_cents: 1000 });
  await page.getByLabel("剪辑风格", { exact: true }).selectOption("upbeat");
  await page.getByRole("button", { name: "重新自动编排", exact: true }).click();
  await expect(page.getByText("已编排横竖两版，可逐镜头微调。")).toBeVisible();
  await page.getByRole("button", { name: /^(比较草稿|收起比较)$/ }).click();
  await page.getByLabel("比较基准草稿", { exact: true }).selectOption("1");
  await expect(comparison).toContainText("旅行纪实");
  await expect(comparison).toContainText("轻快打卡");
  await expect(page.getByLabel("影片标题")).toHaveValue("青山一日 · 湖畔的风");
  expect(drafts.find((item) => item.id === 1)!.data.template_id).toBe("documentary");
  expect(submitted).toEqual({ draft_id: 1, revision: 2 });
  await page.setViewportSize({ width: 1120, height: 900 });
  await comparison.scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(screenshotDir, "scenic-draft-comparison-minimum-window.png"), fullPage: false });
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: /^(比较草稿|收起比较)$/ }).click();
  await page.getByRole("region", { name: "Vlog 剪辑工作台" }).evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: join(screenshotDir, "scenic-vlog-editor-minimum-window.png"), fullPage: false });
  expect(await page.title()).toContain("MyReID");
  expect(page.url()).toBe(url + "/");
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
