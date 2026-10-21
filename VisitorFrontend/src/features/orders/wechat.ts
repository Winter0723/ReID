export type Payment = {
  appId: string;
  timeStamp: string;
  nonceStr: string;
  package: string;
  signType: string;
  paySign: string;
};
declare global {
  interface Window {
    WeixinJSBridge?: {
      invoke: (
        name: string,
        parameters: Payment,
        callback: (result: { err_msg: string }) => void,
      ) => void;
    };
  }
}
export async function invokePayment(
  parameters: Payment,
): Promise<"success" | "cancel"> {
  if (!/MicroMessenger/i.test(navigator.userAgent))
    throw new Error("请在微信中打开此页面完成支付");
  if (!window.WeixinJSBridge)
    await new Promise<void>((resolve, reject) => {
      const ready = () => {
        clearTimeout(timer);
        document.removeEventListener("WeixinJSBridgeReady", ready);
        resolve();
      };
      const timer = setTimeout(() => {
        document.removeEventListener("WeixinJSBridgeReady", ready);
        reject(new Error("微信支付尚未就绪，请重新打开页面"));
      }, 10000);
      document.addEventListener("WeixinJSBridgeReady", ready);
    });
  return new Promise((resolve, reject) =>
    window.WeixinJSBridge!.invoke(
      "getBrandWCPayRequest",
      parameters,
      (result) => {
        if (result.err_msg === "get_brand_wcpay_request:ok") resolve("success");
        else if (result.err_msg === "get_brand_wcpay_request:cancel")
          resolve("cancel");
        else reject(new Error("支付未完成，可刷新订单后重试"));
      },
    ),
  );
}
