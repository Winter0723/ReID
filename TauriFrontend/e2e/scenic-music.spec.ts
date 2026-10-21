import { expect, test } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { DraftRecord, EditingCatalog } from "../src/lib/api/scenicEditing";

// Browser plugin not available. The API fixture serves the actual bundled music files,
// so Chromium must decode and play each shipped track; backend signing/rendering has separate tests.
let server: ViteDevServer;
const url = "http://127.0.0.1:41733";
const musicRoot = resolve("../FastAPIBackend/assets/scenic_music");
const tracks = (JSON.parse(readFileSync(join(musicRoot, "catalog.json"), "utf8")) as {
  tracks: Array<EditingCatalog["music"][number] & { filename: string }>;
}).tracks;
const poster = "data:image/svg+xml," + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#adcbb6"/><path d="M0 250L140 80L290 230L450 55L640 230V360H0" fill="#527d67"/><path d="M0 290Q300 220 640 300V360H0" fill="#609f9d"/></svg>');
const catalog: EditingCatalog = {
  enabled: true,
  templates: [{ id: "documentary", name: "旅行纪实", version: "1", description: "沿游览时间展开", music_id: tracks[0].id, min_clip_seconds: 3.5, max_clip_seconds: 7, color: "#79B6A6" }],
  music: tracks.map((track) => ({ ...track, preview_url: `/api/v1/scenic/editing/music/${track.id}/preview?asset=fixture` })),
};
function draftFixture(): DraftRecord {
  const clip = { id: "clip-1", segment_id: 1, media_id: 1, object_key: "photo.jpg", source_sha256: "hash-1", kind: "image" as const, start_seconds: 0, end_seconds: 5, camera_name: "湖畔栈道", thumbnail_url: poster, original_media_url: poster, locked: false, included: true, keep_audio: false, focus_x: .5, focus_y: .5, crop_mode: "fit" as const };
  return { id: 1, session_id: 1, revision: 1, data: { schema_version: 1, template_id: "documentary", template_version: "1", title: "我的游览记忆", brand: "青山景区", music_id: tracks[0].id, music_volume: .25, original_volume: 1, landscape_seconds: 75, portrait_seconds: 45, sources: [clip], variants: { landscape: [{ ...clip }], portrait: [{ ...clip }] }, warnings: [] } };
}

test.beforeAll(async () => {
  server = await createServer({ configFile: "vite.config.ts", configLoader: "runner", logLevel: "error", server: { host: "127.0.0.1", port: 41733, strictPort: true } });
  await server.listen();
});
test.afterAll(async () => { await server?.close(); });

test("commercial music previews decode, follow volume and persist to the submitted draft", async ({ page }) => {
  const errors: string[] = [];
  const played: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) errors.push(message.text()); });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.addInitScript(() => {
    localStorage.setItem("myreid.scenic.access", "fixture-token");
    localStorage.setItem("myreid.scenic.refresh", "fixture-refresh");
    localStorage.setItem("myreid.preferences", JSON.stringify({ autoRefresh: false }));
  });
  let draft = draftFixture();
  let savedMusic: { id: string; volume: number } | undefined;
  let submitted: Record<string, unknown> | undefined;
  const tourist = { id: 1, ticket_code: "T-001", visitor_label: "林女士", status: "matched", entry_image_url: poster, segment_count: 1, segments: [{ id: 1, included: true }], vlog_jobs: [], orders: [] };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const track = tracks.find((item) => path === `/api/v1/scenic/editing/music/${item.id}/preview`);
    if (track) {
      played.push(track.id);
      await route.fulfill({ contentType: "audio/mpeg", body: readFileSync(join(musicRoot, track.filename)) });
      return;
    }
    let body: unknown = { status: "ok" };
    if (path.endsWith("/readyz")) body = { status: "ready", queue_ready: true, queue_mode: "local" };
    else if (path.endsWith("/auth/me")) body = { username: "editor", role: "admin", email: "editor@example.test" };
    else if (path === "/api/v1/scenic/projects") body = [{ id: 7, name: "青山景区", project_type: "scenic", owner_username: "editor" }];
    else if (path.endsWith("/status")) body = { status: "ready", indexed_targets: 1 };
    else if (path.endsWith("/preview")) body = { previews: [] };
    else if (path.endsWith("/jobs")) body = { jobs: [] };
    else if (path.endsWith("/audit")) body = { audit: [] };
    else if (path.endsWith("/history")) body = { history: [] };
    else if (path.endsWith("/cameras")) body = { devices: [] };
    else if (path.endsWith("/camera-points")) body = { camera_points: [] };
    else if (path.endsWith("/camera-wall")) body = { camera_wall: [] };
    else if (path.endsWith("/media")) body = { media: [] };
    else if (path.endsWith("/dashboard")) body = { project_id: 7, today_sessions: 1, matched_sessions: 1, ready_vlogs: 0 };
    else if (path === "/api/v1/scenic/projects/7/tourists") body = { tourists: [tourist] };
    else if (path.endsWith("/tourists/1")) body = tourist;
    else if (path.endsWith("/editing/catalog")) body = catalog;
    else if (path.endsWith("/drafts") && method === "GET") body = { drafts: [draft] };
    else if (path.endsWith("/drafts/1") && method === "PUT") {
      const payload = route.request().postDataJSON();
      draft = { ...draft, data: payload.data, revision: payload.revision + 1 };
      savedMusic = { id: draft.data.music_id, volume: draft.data.music_volume };
      body = draft;
    } else if (path.endsWith("/vlog")) {
      submitted = route.request().postDataJSON();
      body = { status: "queued", job_id: "fixture-job", vlog: { id: 44, status: "queued" } };
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });

  // Flow under test: scenic editor -> select/audition music -> save/submit the same choice.
  await page.goto(url);
  await page.getByTitle("切换到景区运营").click();
  await page.getByText("林女士", { exact: true }).click();
  await page.getByRole("button", { name: "打开生成视频短片", exact: true }).click();
  const editor = page.getByRole("region", { name: "Vlog 剪辑工作台" });
  await expect(editor).toBeVisible();
  const select = page.getByLabel("配乐", { exact: true });
  const audio = page.getByLabel("配乐试听", { exact: true });
  let previous: Awaited<ReturnType<typeof audio.elementHandle>>;
  for (const track of tracks) {
    await select.selectOption(track.id);
    if (previous) expect(await previous.evaluate((element) => (element as HTMLAudioElement).paused)).toBe(true);
    await expect(page.getByLabel("配乐信息")).toContainText(track.author!);
    await expect(page.getByRole("link", { name: "OpenGameArt.org", exact: true })).toHaveAttribute("href", track.source_url!);
    await expect(page.getByRole("link", { name: "CC0-1.0", exact: true })).toHaveAttribute("href", track.license_url!);
    await audio.evaluate((element) => (element as HTMLAudioElement).play());
    await expect.poll(() => audio.evaluate((element) => (element as HTMLAudioElement).currentTime)).toBeGreaterThan(.1);
    expect(await audio.evaluate((element) => (element as HTMLAudioElement).error)).toBeNull();
    expect(await audio.evaluate((element) => (element as HTMLAudioElement).duration)).toBeGreaterThan(30);
    previous = await audio.elementHandle();
  }
  expect(new Set(played)).toEqual(new Set(tracks.map((track) => track.id)));
  const volume = page.getByLabel("音乐音量", { exact: true });
  await volume.focus();
  await volume.press("Home");
  for (let step = 0; step < 10; step++) await volume.press("ArrowRight");
  await expect(volume).toHaveValue("0.5");
  expect(await audio.evaluate((element) => (element as HTMLAudioElement).volume)).toBe(.5);
  await select.selectOption("none");
  expect(await previous!.evaluate((element) => (element as HTMLAudioElement).paused)).toBe(true);
  await expect(audio).toHaveCount(0);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("草稿已保存，提交后该版本会被冻结。")).toBeVisible();
  expect(savedMusic).toEqual({ id: "none", volume: .5 });

  const finalTrack = tracks[1];
  await select.selectOption(finalTrack.id);
  await audio.evaluate((element) => (element as HTMLAudioElement).play());
  await expect.poll(() => audio.evaluate((element) => (element as HTMLAudioElement).currentTime)).toBeGreaterThan(.1);
  const screenshotDir = process.env.MYREID_QA_SCREENSHOT_DIR || tmpdir();
  await editor.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: join(screenshotDir, "scenic-music-desktop.png"), fullPage: false });
  await page.getByRole("button", { name: "生成横竖双版", exact: true }).click();
  await expect(page.getByText("横版和竖版已分别排队。可继续编辑下一版本。")).toBeVisible();
  expect(savedMusic).toEqual({ id: finalTrack.id, volume: .5 });
  expect(submitted).toEqual({ draft_id: 1, revision: 3 });
  await expect(select).toHaveValue(finalTrack.id);
  await page.setViewportSize({ width: 1120, height: 900 });
  await editor.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.screenshot({ path: join(screenshotDir, "scenic-music-minimum-window.png"), fullPage: false });
  await audio.evaluate((element) => (element as HTMLAudioElement).pause());
  expect(await page.title()).toContain("MyReID");
  expect(page.url()).toBe(url + "/");
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
