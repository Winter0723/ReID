# 源码发布流程

源码发布使用独立 Git 仓库，不继承开发仓库的历史、环境文件或运行数据。首次上传保持 Private；公开前核对许可证、第三方资源和目标部署环境的验收结果。

## 生成完整源码副本

先将拟发布源码纳入 Git 并完成测试，再在仓库根目录执行：

```sh
python scripts/check_public_config.py
python scripts/export_public_source.py --check
python scripts/export_public_source.py --directory ReID --output release/reid-source.zip
```

导出器读取已跟踪文件的当前内容，按白名单排除权重、数据库、密钥、日志、录像和缓存。UTF-8 文本统一为 LF 换行，确保 Windows 导出的脚本也能在 Linux 执行；二进制内容保持原样。输出目录与 ZIP 必须不存在，避免覆盖文件。目录和压缩包都包含 SHA256 文件清单 `SOURCE_MANIFEST.json`。该清单描述导出时的内容；后续编辑后应重新导出。

`ReID/` 是根目录下的完整发布副本，已从父仓库与 Docker 构建上下文排除。进入副本初始化独立仓库，使用 GitHub noreply 邮箱提交，再推送至目标私有仓库。不得复制开发仓库的 `.git` 或使用 `--mirror`。

## 交付内容

- FastAPI 后端、Vue/Tauri 管理端、游客 H5、相机网关、测试与依赖锁文件。
- Docker 编排、数据库迁移、启动、预检、备份与清理工具。
- 全部 `.env*.example` 模板；真实部署配置由部署者生成。
- [空白机器部署](NEW_MACHINE_DEPLOYMENT.md)、[配置参考](CONFIGURATION_REFERENCE.md)、[模型来源](../model/README.md)、[支付限制](WECHAT_PAYMENT_INTEGRATION.md)。
- AGPL-3.0-only 许可证、第三方许可和资源署名。

## 需在目标环境完成的配置

模型权重需另行获取并通过校验。生产环境需配置 SMTP、管理员邮箱、随机密钥、域名与 HTTPS。游客微信登录需有效的应用配置；真实微信支付尚未完成接入验收，默认使用工作人员核实收款。摄像头、GPU、邮件投递、微信授权、备份恢复及真实素材出片须分别验收，不能由单元测试或页面可访问代替。

自动检查应覆盖后端回归、两端单元测试和构建、浏览器业务回归、配置完整性、OpenAPI 文档以及发布边界。记录实际环境、日期与结果，不使用历史通过数量代表新版本验收。

Windows 安装包检查使用 `npm ci` 和 `npm run tauri:build -- --bundles nsis -- --locked`。升级 Tauri 时须同时检查 npm 包与 Rust crate 的主、次版本是否匹配，并提交两端锁文件，不跳过版本一致性检查。静态素材成片使用 FFmpeg 的 H.264 编码路径；Linux 环境不依赖 OpenCV 提供 H.264 写入器。
