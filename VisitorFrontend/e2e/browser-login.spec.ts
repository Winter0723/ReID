import { expect, test } from "@playwright/test";

for (const width of [390, 1120]) {
  test(`浏览器免微信登记及刷新 ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    let signedIn = false;
    let logins = 0;
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
    await page.route("**/api/v1/scenic/visitor/**", async route => {
      const path = new URL(route.request().url()).pathname.split("/visitor")[1];
      if (path === "/runtime") return route.fulfill({ json: { auth_provider: "browser", payment_provider: "manual", testing: false } });
      if (path === "/sites/1") return route.fulfill({ json: { id: 1, name: "青山景区", price_cents: 1990, consent_text: "同意拍摄", consent_version: "visitor-v1" } });
      if (path === "/auth/browser") {
        expect(route.request().method()).toBe("POST");
        expect(route.request().postDataJSON()).toEqual({ project_id: 1 });
        expect(route.request().headers()["x-visitor-request"]).toBe("1");
        signedIn = true;
        logins++;
        return route.fulfill({ json: { authenticated: true } });
      }
      if (path === "/me") return route.fulfill({ status: signedIn ? 200 : 401, json: { authenticated: signedIn } });
      if (path === "/visits") return route.fulfill({ json: { visits: [] } });
      throw new Error(`Unexpected visitor request: ${path}`);
    });
    await page.goto("/visitor/#/sites/1");
    await expect(page.getByText("无需微信授权", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "开始登记" }).click();
    await expect(page.getByText("＋ 拍摄或选择照片")).toBeVisible();
    await expect(page).toHaveURL(/#\/sites\/1\/register$/);
    await page.reload();
    await expect(page.getByText("＋ 拍摄或选择照片")).toBeVisible();
    // A direct registration link must also work in a fresh browser session.
    signedIn = false;
    await page.reload();
    await expect(page.getByText("＋ 拍摄或选择照片")).toBeVisible();
    expect(signedIn).toBe(true);
    expect(logins).toBeGreaterThanOrEqual(3);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`browser-registration-${width}.png`), fullPage: true });
    expect(errors).toEqual([]);
  });
}
