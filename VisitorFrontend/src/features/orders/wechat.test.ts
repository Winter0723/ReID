// @vitest-environment jsdom
import { afterEach, expect, test, vi } from "vitest";
import { invokePayment, type Payment } from "./wechat";
const parameters = {} as Payment;
afterEach(() => {
  vi.restoreAllMocks();
  delete window.WeixinJSBridge;
});
test("non-WeChat browser cannot initiate JSAPI", async () => {
  await expect(invokePayment(parameters)).rejects.toThrow("请在微信中打开");
});
test("cancel is a distinct state and does not imply payment success", async () => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("MicroMessenger");
  window.WeixinJSBridge = {
    invoke: (_name, _params, callback) =>
      callback({ err_msg: "get_brand_wcpay_request:cancel" }),
  };
  expect(await invokePayment(parameters)).toBe("cancel");
});
