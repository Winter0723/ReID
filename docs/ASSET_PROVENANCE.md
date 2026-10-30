# 静态资产来源

- `VisitorFrontend/public/images/mountains.svg`：本项目绘制的几何山景 SVG，无人物或外部图像。
- `TauriFrontend/src-tauri/app-icon.svg`：本项目绘制的几何 M 标识。图标由本地锁定的 Tauri CLI
  执行 `npm exec -- tauri icon src-tauri/app-icon.svg --output src-tauri/icons` 生成。
- `VisitorFrontend/e2e/fixtures/preview.mp4`：FFmpeg 合成图案，不包含真实摄像头素材：
  `ffmpeg -f lavfi -i testsrc2=size=320x180:rate=12 -t 2 -c:v libx264 -pix_fmt yuv420p -movflags +faststart preview.mp4`。
- 以上新资产按项目 AGPL-3.0-only 许可证发布。
- 配乐保持原来源与授权记录，见 `FastAPIBackend/assets/scenic_music/LICENSE_SOURCES.md`，
  运行测试会核对 catalog、校验值与分发文件，第三方音乐遵守各自许可。
- 模型词表及上游代码保留原许可；模型权重不随源码发行。

- `docs/assets/sponsorship/alipay.jpg` 与 `wechat.png`：项目维护者提供并授权公开的赞助收款码，原图复制，未重绘或修改二维码。仅用于项目赞助展示，不作为应用内业务订单付款入口。
