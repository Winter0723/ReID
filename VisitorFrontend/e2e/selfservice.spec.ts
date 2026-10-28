import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
const video = readFileSync(new URL("./fixtures/preview.mp4", import.meta.url));
const pixel =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==";
for (const width of [390, 1120]) {
  test(`游客自助挑选、编辑、制作、付款确认及下载 ${width}`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.setViewportSize({ width, height: 844 });
    let state: any = {
      id: 7,
      project_id: 1,
      name: "青山景区",
      created_at: "2026-10-13T01:00:00Z",
      checked_out_at: null,
      stage: "visiting",
      message: "请挑选本人的游玩片段",
      price_cents: 1990,
      video_available: false,
      expired: false,
      order: null,
      selection_available: true,
      payment_provider: "manual",
    };
    let draft: any = null,
      latestEdit: any = null,
      selected: number[] = [],
      ready = false,
      confirmed = false,
      renders = 0;
    const segments = [71, 72, 73].map((id, index) => ({
      id,
      camera_name: `游玩点位 ${index + 1}`,
      similarity: 0.94,
      start_seconds: 0,
      end_seconds: 3,
      included: true,
      thumbnail_url: pixel,
      preview_url: `/api/v1/scenic/visitor/visits/7/segments/${id}/preview`,
    }));
    const copy = (x: any) => JSON.parse(JSON.stringify(x));
    await page.route("**/api/v1/scenic/visitor/**", async (route) => {
      const path = new URL(route.request().url()).pathname.replace(
          "/api/v1/scenic/visitor",
          "",
        ),
        method = route.request().method();
      if (path === "/runtime")
        return route.fulfill({
          json: {
            auth_provider: "browser",
            browser_login_available: true,
            wechat_login_available: true,
            payment_provider: "manual",
            testing: false,
          },
        });
      if (path === "/me")
        return route.fulfill({ json: { authenticated: true, auth_provider: width === 390 ? "browser" : "wechat" } });
      if (path === "/visits/7/finish") {
        state = { ...state, stage: "selecting", checked_out_at: "2026-10-13T02:00:00Z" };
        return route.fulfill({ json: state });
      }
      if (path === "/visits/7/editor")
        return route.fulfill({
          json: {
            editable: true,
            match_revision: "matched-v1",
            segments,
            draft,
            catalog: {
              enabled: true,
              tracking_enabled: false,
              templates: [
                { id: "documentary", name: "旅行纪实" },
                { id: "memory", name: "温情回忆" },
              ],
              music: [],
            },
          },
        });
      if (path === "/visits/7/draft" && method === "POST") {
        const body = route.request().postDataJSON();
        selected = body.segment_ids;
        const sources = selected.map((id) => ({
          id: `s-${id}`,
          kind: "video",
          camera_name: `游玩点位 ${id - 70}`,
          start_seconds: 0,
          end_seconds: 2,
          available_start_seconds: 0,
          available_end_seconds: 3,
          included: true,
          locked: false,
          keep_audio: false,
          has_audio: false,
          focus_x: 0.5,
          focus_y: 0.5,
          crop_mode: "fit",
          title: "",
          thumbnail_url: pixel,
          preview_url: `/api/v1/scenic/visitor/visits/7/draft/sources/s-${id}/preview`,
        }));
        draft = {
          id: 11,
          revision: 1,
          data: {
            ...body.config,
            sources,
            variants: { landscape: copy(sources), portrait: copy(sources) },
            cover_clip_id: sources[0].id,
            warnings: [],
          },
        };
        state = { ...state, stage: "editing" };
        return route.fulfill({ json: { visit: state, draft } });
      }
      if (path === "/visits/7/draft" && method === "PUT") {
        latestEdit = route.request().postDataJSON();
        draft = {
          ...draft,
          revision: draft.revision + 1,
          data: latestEdit.data,
        };
        return route.fulfill({ json: { visit: state, draft } });
      }
      if (path === "/visits/7/render") {
        expect(route.request().postDataJSON().revision).toBe(draft.revision);
        renders++;
        state = {
          ...state,
          stage: "rendering",
          selection_available: false,
          render_progress: [
            {
              orientation: "landscape",
              status: "queued",
              progress: 0,
              message: "排队中",
            },
            {
              orientation: "portrait",
              status: "running",
              progress: 0.2,
              message: "制作中",
            },
          ],
        };
        return route.fulfill({ json: state });
      }
      if (path === "/visits/7/pay") {
        state = {
          ...state,
          order: {
            id: 42,
            order_no: "SELF-42",
            status: "pending",
            payment_provider: "visitor_manual",
            amount_cents: 1990,
            expires_at: "2027-10-13T00:00:00Z",
            claimed: false,
          },
        };
        return route.fulfill({
          json: { visit: state, payment: { provider: "manual" } },
        });
      }
      if (path === "/visits/7/payment-status") {
        if (confirmed)
          state = {
            ...state,
            order: { ...state.order, status: "paid" },
            download_urls: {
              landscape: "/api/v1/scenic/visitor/downloads/7/42/landscape",
              portrait: "/api/v1/scenic/visitor/downloads/7/42/portrait",
            },
          };
        return route.fulfill({ json: state });
      }
      if (path === "/visits/7/claim") {
        state = { ...state, order: { ...state.order, claimed: true } };
        return route.fulfill({ json: state });
      }
      if (path === "/visits/7") {
        if (ready && state.stage === "rendering")
          state = {
            ...state,
            stage: "ready",
            video_available: true,
            render_progress: [],
          };
        return route.fulfill({ json: state });
      }
      if (path.startsWith("/downloads/"))
        return route.fulfill({
          contentType: "video/mp4",
          headers: {
            "Content-Disposition":
              'attachment; filename="visitor-landscape.mp4"',
          },
          body: video,
        });
      if (path.includes("/preview") || path.includes("/video/"))
        return route.fulfill({ contentType: "video/mp4", body: video });
      return route.fulfill({
        status: 404,
        json: { detail: "missing fixture" },
      });
    });
    await page.goto("/visitor/#/visits/7");
    await expect(page.getByText(width === 390 ? "当前身份：浏览器登录。" : "当前身份：微信登录", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "结束游览，匹配我的画面" }).click();
    await page.getByRole("button", { name: "结束并匹配", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "我的短片工作室" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "预览候选片段 1", exact: true })
      .click();
    await expect(page.locator(".clip-preview video")).toBeVisible();
    await expect(page.locator(".clip-preview video")).toBeInViewport();
    await page.getByRole("button", { name: "关闭预览" }).click();
    await page.getByRole("checkbox", { name: "保留片段 3" }).uncheck();
    await page.getByLabel("短片标题").fill("我的自助旅程");
    await page.getByLabel("背景音乐").selectOption("none");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (process.env.VISITOR_QA_DIR)
      await page.screenshot({
        path: `${process.env.VISITOR_QA_DIR}/visitor-self-selection-${width}.png`,
        fullPage: true,
      });
    await page.getByRole("button", { name: "确认片段，编排短片" }).click();
    expect(selected).toEqual([71, 72]);
    await page.getByRole("button", { name: "后移镜头 1", exact: true }).click();
    await page
      .getByRole("spinbutton", { name: "镜头 1 结束秒数", exact: true })
      .fill("1.5");
    await page.getByRole("button", { name: "竖版", exact: true }).click();
    await page
      .getByRole("combobox", { name: "镜头 1 构图", exact: true })
      .selectOption("focus");
    await page
      .getByRole("slider", { name: "镜头 1 横向焦点", exact: true })
      .fill("0.3");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page.getByText("草稿已保存，可以关闭页面后继续编辑。", { exact: false }),
    ).toBeVisible();
    expect(latestEdit.data.title).toBe("我的自助旅程");
    expect(latestEdit.data.variants.landscape[0].id).toBe("s-72");
    expect(latestEdit.data.variants.landscape[0].end_seconds).toBe(1.5);
    expect(latestEdit.data.variants.portrait[0].focus_x).toBe(0.3);
    await page.reload();
    await expect(
      page.getByRole("combobox", { name: "镜头 1 构图", exact: true }),
    ).toHaveValue("fit");
    await page.getByRole("button", { name: "竖版", exact: true }).click();
    await expect(
      page.getByRole("combobox", { name: "镜头 1 构图", exact: true }),
    ).toHaveValue("focus");
    await page
      .getByRole("spinbutton", { name: "镜头 1 结束秒数", exact: true })
      .fill("0.5");
    await page.getByRole("button", { name: "预览镜头 1", exact: true }).click();
    const clipVideo = page.locator(".clip-preview video");
    await expect(clipVideo).toBeInViewport();
    await expect(clipVideo).toHaveAttribute(
      "src",
      /start_seconds=0&end_seconds=0.5/,
    );
    await clipVideo.evaluate((video: HTMLVideoElement) => video.play());
    await expect
      .poll(
        () => clipVideo.evaluate((video: HTMLVideoElement) => video.paused),
        { timeout: 5000 },
      )
      .toBe(true);
    expect(
      await clipVideo.evaluate((video: HTMLVideoElement) => video.currentTime),
    ).toBeGreaterThanOrEqual(0.49);
    expect(
      await clipVideo.evaluate((video: HTMLVideoElement) => video.currentTime),
    ).toBeLessThan(0.9);
    await page.getByRole("button", { name: "关闭预览" }).click();
    await page
      .getByRole("spinbutton", { name: "镜头 1 结束秒数", exact: true })
      .fill("2");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (process.env.VISITOR_QA_DIR)
      await page.screenshot({
        path: `${process.env.VISITOR_QA_DIR}/visitor-self-editor-${width}.png`,
        fullPage: true,
      });
    await page
      .getByRole("button", { name: "确认制作横竖版", exact: true })
      .click();
    await expect(page.getByText("横版 · 排队中 · 0%")).toBeVisible();
    await page.reload();
    await expect(page.getByText("竖版 · 制作中 · 20%")).toBeVisible();
    ready = true;
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "视频试看", exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "申请付款确认 · ¥19.90", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "申请已提交，等待工作人员确认" }),
    ).toBeDisabled();
    await expect(
      page.getByRole("link", { name: "下载视频", exact: true }),
    ).toHaveCount(0);
    confirmed = true;
    await page.getByRole("button", { name: "刷新工作人员确认结果" }).click();
    const download = page.waitForEvent("download");
    await page.getByRole("link", { name: "下载视频", exact: true }).click();
    expect((await download).suggestedFilename()).toBe("visitor-landscape.mp4");
    await page.getByRole("button", { name: "我已保存，确认领取" }).click();
    await expect(
      page.getByRole("button", { name: "已确认领取" }),
    ).toBeDisabled();
    expect(renders).toBe(1);
    expect(errors).toEqual([]);
  });
}
