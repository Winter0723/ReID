# 微信支付：保留代码与接入前阻断项

本次开源整理**没有修复或验证真实微信支付实现**，也没有真实商户凭据。
`FastAPIBackend/app/services/payments.py` 保持原样。不要把示例配置改为真实支付后直接上线。
默认 `PAYMENT_PROVIDER=manual`、`VISITOR_PAYMENT_PROVIDER=manual`；人工确认只表示
工作人员在系统外核实收款，并不证明微信发生了扣款。开发可使用 `.env.visitor-test.example`。

生产与预发布 Compose 默认不挂载微信证书，因此人工模式不需要伪造 PEM 文件。
完成下述修复后，才在部署命令末尾叠加 `-f docker-compose.wechat.yml` 并提供真实证书。

## 有商户配置后必须修改的代码

1. `WechatNativePaymentClient.__init__` 中 `_platform_public_key` 缓存字段与同名加载方法冲突。
   将缓存改名为 `_cached_platform_public_key`，同步修改加载/缓存逻辑。
2. `WechatNativePaymentClient._request` 必须在 JSON 解析前，按官方协议对**原始响应字节**验签。
   检查 `Wechatpay-Timestamp`、`Wechatpay-Nonce`、`Wechatpay-Serial`、`Wechatpay-Signature`，
   拒绝缺签、错误公钥 ID、过期时间戳、畸形 Base64 和被篡改响应。证书轮换不得回退为跳过验签。
3. 通知解密后的 `appid`、`mchid`、`out_trade_no`、`transaction_id`、金额、币种必须与
   当前商户和本地订单一致；查询响应也必须校验。通知幂等及交易号唯一性要覆盖并发。
4. `app/api/scenic/order_routes.py` 与 `visitor_routes.py` 需要联合验证通知、主动查单、
   重复付款请求、断网重试和关闭订单。过期未付订单应先关单再重建，不能误将超时视为失败收款。
5. 使用**真实 RSA 签名及 AES-GCM 加密的本地合成测试**覆盖成功、伪造、重放、错误商户、
   错误金额、公钥切换、重复回调。现有 fake 客户端测试不满足以上要求。
6. 最后使用自己的商户测试环境或受控小额订单验证支付、通知、查单、退款与对账。

官方依据：[微信支付公钥验签说明](https://pay.wechatpay.cn/doc/v3/merchant/4013053249)。
按部署时官方最新协议实现，不要直接信任本文件中的字段枚举已覆盖所有未来变更。

## 配置与文件对应

| 配置 | 内容 / 存放位置 |
| --- | --- |
| `WECHAT_PAY_APPID` | 与公众号及游客 OAuth 使用的 AppID 对齐 |
| `WECHAT_PAY_MCHID` | 已开通对应产品的商户号 |
| `WECHAT_PAY_API_V3_KEY` | 商户平台 API v3 密钥；仅放私有环境变量 |
| `WECHAT_PAY_MERCHANT_SERIAL_NO` | 与私钥对应的商户证书序列号 |
| `WECHAT_PAY_MERCHANT_PRIVATE_KEY_PATH` | 容器内只读私钥路径，例 `/run/secrets/wechat_merchant_private_key.pem` |
| `WECHAT_PAY_MERCHANT_PRIVATE_KEY_FILE` | Compose 宿主机私钥路径，例 `./secrets/wechat_merchant_private_key.pem` |
| `WECHAT_PAY_PLATFORM_PUBLIC_KEY_ID` | 微信支付公钥 ID，必须与响应/通知 serial 匹配 |
| `WECHAT_PAY_PLATFORM_PUBLIC_KEY_PATH` / `..._FILE` | 容器 / 宿主机平台公钥文件路径 |
| `PAYMENT_PUBLIC_BASE_URL` | 可从微信访问的 HTTPS API 基址 |
| `VISITOR_PUBLIC_BASE_URL` | 游客 HTTPS 同源站点；配置 OAuth 回调与支付目录 |
| `VISITOR_WECHAT_APPID` / `VISITOR_WECHAT_SECRET` | OAuth 登录应用信息，与支付密钥不同 |
| `PAYMENT_PROVIDER` / `VISITOR_PAYMENT_PROVIDER` | 完成上述修复及联调后才分别设为 `wechat_native` / `wechat` |

回调路由以 `docs/API.md` 和运行版本 OpenAPI 为准。反向代理只公开必要游客路由；
后台 API 和对象存储不得直接暴露。HTTP 客户端日志级别已收紧，但应另行检查以往部署日志，
确认 OAuth secret/code 是否已留存，必要时撤销或轮换。真实配置和 PEM 文件不能进入源码包。
