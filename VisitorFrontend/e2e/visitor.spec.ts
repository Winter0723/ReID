import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const previewVideo = readFileSync(
  new URL("./fixtures/preview.mp4", import.meta.url),
);
const site = {
  id: 1,
  name: "青山景区",
  description:
    "在这里，我们为你记录此行的美好瞬间。用影像留住风景，也留住此刻的自己。",
  price_cents: 1990,
  consent_version: "visitor-v1",
  consent_text:
    "我同意本景区仅在本次游览期间使用我的照片及拍摄画面进行人物匹配、视频制作与交付。",
};
const initial = {
  id: 7,
  project_id: 1,
  name: "青山景区",
  created_at: "2026-09-29T01:12:00Z",
  checked_out_at: null,
  stage: "visiting",
  message: "正在游览中",
  price_cents: 1990,
  video_available: false,
  expired: false,
  order: null as any,
};
async function mock(page: Page, stage = "visiting") {
  let visit = { ...initial, stage, video_available: stage === "ready" };
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" || message.type() === "warning")
      errors.push(message.text());
  });
  await page.route("**/api/v1/scenic/visitor/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(
      "/api/v1/scenic/visitor",
      "",
    );
    if (path === "/runtime") return route.fulfill({ json: { auth_provider: "wechat", payment_provider: "wechat", testing: false } });
    if (path === "/me") return route.fulfill({ json: { authenticated: true } });
    if (path === "/sites/1") return route.fulfill({ json: site });
    if (path === "/sites/1/visits") return route.fulfill({ json: visit });
    if (path === "/visits") return route.fulfill({ json: { visits: [visit] } });
    if (path === "/visits/7/finish") {
      visit = { ...visit, stage: "sealing", message: "结束游览已受理，正在封存本次录像" };
      return route.fulfill({ json: visit });
    }
    if (path === "/visits/7") return route.fulfill({ json: visit });
    if (path.includes("/video/"))
      return route.fulfill({
        status: 200,
        contentType: "video/mp4",
        body: previewVideo,
      });
    if (path === "/visits/7/pay")
      return route.fulfill({
        json: {
          visit: {
            ...visit,
            download_urls: {
              landscape:
                "/api/v1/scenic/visitor/downloads/7/1/landscape?asset_expires=123&asset_signature=test",
              portrait:
                "/api/v1/scenic/visitor/downloads/7/1/portrait?asset_expires=123&asset_signature=test",
            },
            order: {
              id: 1,
              status: "paid",
              order_no: "SV-DEMO",
              amount_cents: 1990,
              expires_at: "2026-10-29T00:00:00Z",
              claimed: false,
            },
          },
          payment: null,
        },
      });
    if (path === "/visits/7/claim") {
      visit = {
        ...visit,
        order: {
          id: 1,
          status: "paid",
          order_no: "SV-DEMO",
          amount_cents: 1990,
          expires_at: "2026-10-29T00:00:00Z",
          claimed: true,
        },
      };
      return route.fulfill({ json: visit });
    }
    return route.fulfill({ status: 404, json: { detail: "不存在" } });
  });
  return errors;
}
test("局域网 HTTP 微信会话、照片框选与授权登记", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window.crypto, "randomUUID", { value: undefined, configurable: true });
  });
  const errors = await mock(page);
  await page.goto("/visitor/#/sites/1");
  await expect(page).toHaveTitle("景区留影 · 我的游览回忆");
  await expect(
    page.getByRole("heading", { name: "游览影像登记" }),
  ).toBeVisible();
  await expect(page.getByText("¥19.90", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "开始登记", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "上传本人照片" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "提交登记" })).toBeDisabled();
  const key = await page.evaluate(() => sessionStorage.getItem("visitor.registration.1"));
  expect(key).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("heading", { name: "上传本人照片" })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("visitor.registration.1"))).toBe(key);
  await page.locator("input[type=file]").setInputFiles({
    name: "photo.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWQAAAABJRU5ErkJggg==",
      "base64",
    ),
  });
  await page.getByRole("button", { name: "照片只有我，选择整张照片" }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "提交登记" }).click();
  await expect(
    page.getByRole("heading", { name: "您已进入青山景区" }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
test("结束游览与重新打开保留真实进度", async ({ page }) => {
  await mock(page);
  await page.goto("/visitor/#/visits/7");
  await page.getByRole("button", { name: "结束游览，匹配我的画面" }).click();
  await page.getByRole("button", { name: "结束并匹配", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "匹配与制作进度", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText("结束游览已受理，正在封存本次录像")).toBeVisible();
  await expect(page.locator(".stages li.active")).toHaveText(/封存录像/);
  await expect(page.getByRole("progressbar", { name: "结束游览已受理，正在封存本次录像" })).toBeVisible();
  if (process.env.VISITOR_QA_DIR) await page.screenshot({ path: `${process.env.VISITOR_QA_DIR}/visitor-sealing-wait.png`, fullPage: true });
  expect(await page.locator("body").innerText()).not.toMatch(/\d+%|预计一分钟/);
});
test("处理失败可由游客自行重试", async ({ page }) => {
  await mock(page, "review");
  await page.goto("/visitor/#/visits/7");
  await expect(
    page.getByRole("heading", { name: "本次处理未完成" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "结束游览，匹配我的画面" }),
  ).toHaveCount(0);
});
test("结束请求已受理但响应丢失时恢复封存进度", async ({ page }) => {
  await mock(page);
  let accepted = false;
  await page.route('**/visits/7/finish', async route => {
    accepted = true;
    await route.abort('connectionreset');
  });
  await page.route('**/visits/7', async route => {
    await route.fulfill({json:{...initial,stage:accepted?'sealing':'visiting',message:accepted?'结束游览已受理，正在封存本次录像':'正在游览中'}});
  });
  await page.goto('/visitor/#/visits/7');
  await page.getByRole('button',{name:'结束游览，匹配我的画面'}).click();
  await page.getByRole('button',{name:'结束并匹配',exact:true}).click();
  await expect(page.locator('.stages li.active')).toHaveText(/封存录像/);
  await expect(page.locator('.notice.error')).toHaveCount(0);
});
test("试看画幅切换和已付订单领取", async ({ page }) => {
  const errors = await mock(page, "ready");
  await page.goto("/visitor/#/visits/7");
  await expect(page.locator("video")).toHaveAttribute(
    "src",
    /landscape\/preview$/,
  );
  await expect
    .poll(() =>
      page
        .locator("video")
        .evaluate((video: HTMLVideoElement) => video.readyState),
    )
    .toBeGreaterThan(0);
  await page.getByRole("button", { name: "竖屏", exact: true }).click();
  await expect(page.locator("video")).toHaveAttribute(
    "src",
    /portrait\/preview$/,
  );
  await page.getByRole("button", { name: "购买完整视频 · ¥19.90" }).click();
  await expect(
    page.getByRole("heading", { name: "已购视频", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "下载视频", exact: true }),
  ).toHaveAttribute(
    "href",
    /downloads\/7\/1\/portrait\?asset_expires=123&asset_signature=test$/,
  );
  await page.getByRole("button", { name: "我已保存，确认领取" }).click();
  await expect(
    page.getByRole("button", { name: "已确认领取", exact: true }),
  ).toBeDisabled();
  expect(errors).toEqual([]);
});
test("401 提示从景区二维码重新登录，不展示他人记录", async ({ page }) => {
  await page.route("**/api/v1/scenic/visitor/**", (r) =>
    new URL(r.request().url()).pathname.endsWith("/runtime")
      ? r.fulfill({ json: { auth_provider: "wechat", payment_provider: "wechat", testing: false } })
      : r.fulfill({ status: 401, json: { detail: "请在微信中登录后继续" } }),
  );
  await page.goto("/visitor/#/visits");
  await expect(page.getByText("从景区入口开始", { exact: true })).toBeVisible();
  await expect(page.locator(".visit-row")).toHaveCount(0);
});
test("手机和桌面布局无横向溢出", async ({ page }) => {
  const errors = await mock(page);
  for (const width of [320, 390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/visitor/#/sites/1");
    await expect(
      page.getByRole("button", { name: "开始登记", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    if (width === 390 && process.env.VISITOR_QA_DIR)
      await page.screenshot({
        path: `${process.env.VISITOR_QA_DIR}/visitor-welcome.png`,
        fullPage: true,
      });
  }
  expect(errors).toEqual([]);
});


test("test login and checkout cover cancellation, failure, delay and duplicate", async ({ page }) => {
  const errors = await mock(page, "ready");
  await page.route("**/api/v1/scenic/visitor/runtime", route => route.fulfill({ json: { auth_provider: "test", payment_provider: "test", testing: true } }));
  let selected = "";
  await page.route("**/api/v1/scenic/visitor/auth/test", async route => {
    const body = route.request().postDataJSON();
    selected = body.visitor;
    expect(body.invite_code).toBe("integration-invite-secret");
    await route.fulfill({ json: { authenticated: true } });
  });
  await page.goto("/visitor/#/sites/1");
  await expect(page.getByText("测试环境，不产生真实扣款", { exact: true })).toBeVisible();
  if (process.env.VISITOR_QA_DIR) await page.screenshot({ path: `${process.env.VISITOR_QA_DIR}/visitor-test-login.png`, fullPage: true });
  await page.getByLabel("测试邀请码").fill("integration-invite-secret");
  await page.getByLabel("测试游客").selectOption("B");
  await page.getByRole("button", { name: "开始登记", exact: true }).click();
  await expect(page).toHaveURL(/register/);
  expect(selected).toBe("B");
  let paid = false;
  const order = { id: 1, status: "pending", amount_cents: 1990, order_no: "TEST-ORDER", expires_at: "2099-01-01T00:00:00Z", claimed: false };
  const result = () => ({ ...initial, stage: "ready", video_available: true, order: { ...order, status: paid ? "paid" : "pending" } });
  await page.route("**/api/v1/scenic/visitor/visits/7", route => route.fulfill({ json: result() }));
  await page.route("**/api/v1/scenic/visitor/visits/7/pay", route => route.fulfill({ json: { visit: result(), payment: { provider: "test", token: "server-checkout-token", amount_cents: 1990, currency: "CNY" } } }));
  let outcome = "";
  await page.route("**/api/v1/scenic/visitor/visits/7/test-payment", async route => {
    const body = route.request().postDataJSON();
    expect(body.token).toBe("server-checkout-token");
    expect(body.amount_cents).toBeUndefined();
    outcome = body.outcome;
    if (outcome === "success") paid = true;
    await route.fulfill({ json: result() });
  });
  await page.route("**/api/v1/scenic/visitor/visits/7/payment-status", route => {
    if (outcome === "delayed") paid = true;
    return route.fulfill({ json: result() });
  });
  await page.goto("/visitor/#/visits/7");
  await page.getByRole("button", { name: /购买完整视频/ }).click();
  const cashier = page.getByRole("region", { name: "模拟支付" });
  await expect(cashier).toBeVisible();
  await expect(cashier).toContainText("¥19.90");
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (process.env.VISITOR_QA_DIR) await page.screenshot({ path: `${process.env.VISITOR_QA_DIR}/visitor-test-checkout-${width}.png`, fullPage: true });
  }
  await expect(cashier.getByRole("button", { name: "重复通知", exact: true })).toBeDisabled();
  await cashier.getByRole("button", { name: "取消", exact: true }).click();
  await expect(cashier).toContainText("已取消支付，订单已保留");
  await cashier.getByRole("button", { name: "失败", exact: true }).click();
  await expect(cashier).toContainText("模拟支付失败");
  await cashier.getByRole("button", { name: /延迟到账/ }).click();
  await expect(cashier).toContainText("15 秒后刷新");
  await cashier.getByRole("button", { name: "刷新到账结果" }).click();
  await expect(page.getByRole("link", { name: "下载视频", exact: true })).toBeVisible();
  await cashier.getByRole("button", { name: "重复通知", exact: true }).click();
  await expect(cashier).toContainText("订单保持已支付");
  await expect(page).toHaveTitle(/景区/);
  expect(errors).toEqual([]);
});


test("manual payment waits for staff before download and claim", async ({ page }) => {
  const errors = await mock(page, "ready");
  let requested = false, confirmed = false, claimed = false;
  const result = () => ({ ...initial, stage: "ready", video_available: true, payment_provider: "manual",
    order: requested ? { id: 1, status: confirmed ? "paid" : "pending", payment_provider: "visitor_manual", order_no: "MANUAL-001", amount_cents: 1990, expires_at: "2099-01-01T00:00:00Z", claimed } : null });
  await page.route("**/api/v1/scenic/visitor/visits/7", route => route.fulfill({ json: result() }));
  await page.route("**/api/v1/scenic/visitor/visits/7/pay", route => {
    requested = true;
    return route.fulfill({ json: { visit: result(), payment: { provider: "manual" } } });
  });
  await page.route("**/api/v1/scenic/visitor/visits/7/payment-status", route => route.fulfill({ json: result() }));
  await page.route("**/api/v1/scenic/visitor/visits/7/claim", route => { claimed = true; return route.fulfill({ json: result() }); });
  await page.goto("/visitor/#/visits/7");
  await expect(page).toHaveTitle(/景区/);
  await page.getByRole("button", { name: /申请付款确认/ }).click();
  await expect(page.getByRole("button", { name: "申请已提交，等待工作人员确认" })).toBeDisabled();
  await expect(page.getByRole("link", { name: "下载视频", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "刷新工作人员确认结果" }).click();
  await expect(page.getByRole("link", { name: "下载视频", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "申请已提交，等待工作人员确认" })).toBeDisabled();
  for (const width of [390, 1120]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (process.env.VISITOR_QA_DIR) await page.screenshot({ path: `${process.env.VISITOR_QA_DIR}/manual-payment-${width}.png`, fullPage: true });
  }
  confirmed = true;
  // Staff confirmation propagates automatically without a manual refresh.
  await expect(page.getByRole("link", { name: "下载视频", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "我已保存，确认领取" }).click();
  await expect(page.getByRole("button", { name: "已确认领取" })).toBeDisabled();
  expect(errors).toEqual([]);
});

test("WeChat download enters a capability page usable in a fresh browser", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, userAgent: "Mozilla/5.0 iPhone MicroMessenger/8.0" });
  const page = await context.newPage();
  const errors = await mock(page, "ready");
  const receipt = "/api/v1/scenic/visitor/deliveries/fixture-private-receipt/page";
  const full = "/api/v1/scenic/visitor/deliveries/fixture-private-receipt/variants/1/full?download=true";
  await page.route("**/api/v1/scenic/visitor/visits/7", route => route.fulfill({ json: {
    ...initial, stage: "ready", video_available: true, delivery_page_url: receipt,
    order: { id: 1, status: "paid", order_no: "MANUAL-001", expires_at: "2099-01-01T00:00:00Z", claimed: false },
  } }));
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>双版领取页</title><h1>领取横竖完整视频</h1><p>无需再次微信登录</p><a href="${full}">保存横版</a></html>`;
  await page.route(`**${receipt}`, route => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto("/visitor/#/visits/7");
  await expect(page.getByRole("link", { name: "下载视频", exact: true })).toHaveAttribute("href", receipt);
  await page.getByRole("link", { name: "下载视频", exact: true }).click();
  await expect(page).toHaveTitle("双版领取页");
  // A completely separate cookie jar receives the same capability URL.
  const external = await browser.newContext({ viewport: { width: 390, height: 844 } });
  try {
    expect(await external.cookies()).toEqual([]);
    await external.route(`**${receipt}`, route => route.fulfill({ contentType: "text/html", body: html }));
    await external.route(`**${full}`, route => route.fulfill({ contentType: "video/mp4", headers: { "Content-Disposition": 'attachment; filename="visitor-landscape.mp4"' }, body: previewVideo }));
    const tab = await external.newPage();
    await tab.goto(page.url());
    await expect(tab.getByRole("heading", { name: "领取横竖完整视频" })).toBeVisible();
    const download = tab.waitForEvent("download");
    await tab.getByRole("link", { name: "保存横版" }).click();
    expect((await download).suggestedFilename()).toBe("visitor-landscape.mp4");
    expect(errors).toEqual([]);
  } finally { await external.close(); await context.close(); }
});
