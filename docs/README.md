# 文档导航

首次使用从[根目录上手攻略](../README.md)开始，按获取源码、放置模型、Docker 启动、配置邮箱及使用功能的顺序操作。
需要配置详情、正式部署或 HTTPS 时，再阅读[空白机器部署与必填配置](NEW_MACHINE_DEPLOYMENT.md)。

本目录以 `allinone` 分支的 FastAPI 后端、Tauri 运营工作台和游客 H5 为维护范围。带日期的性能、硬件和验收记录仅描述当时的环境，不代表当前机器或新部署已经通过验收。

## 按任务阅读

| 要做什么 | 文档 |
| --- | --- |
| 第一次了解项目、找到代码和运行产物 | [项目总指南](PROJECT_GUIDE.md) |
| 从零试用、启动、配置邮箱和使用功能 | [根目录上手攻略](../README.md) |
| 准备模型文件、检查模型及注册可选模型 | [模型说明](../model/README.md) |
| 配置 Docker local / production、HTTPS 及新机器验收 | [空白机器部署](NEW_MACHINE_DEPLOYMENT.md) |
| 选择配置模板、排查变量优先级和端口 | [配置参考](CONFIGURATION.md) |
| 安装依赖、修改代码、运行测试 | [开发指南](DEVELOPMENT.md) |
| 部署服务、升级、备份与恢复 | [部署指南](DEPLOYMENT.md) |
| Windows + Docker + NVIDIA 本机生产栈 | [本机生产指南](local-production.md) |
| 查询 HTTP 接口、身份权限和实时流契约 | [API 与通信](API.md) |
| 检查两个数据库的迁移与历史表 | [数据库拆分运行手册](DATABASE_SPLIT_RUNBOOK.md) |
| 开发或打包运营工作台 | [TauriFrontend](../TauriFrontend/README.md) |
| 开发游客 H5 | [VisitorFrontend](../VisitorFrontend/README.md) |
| 配置游客登录、制作、支付和领取 | [游客自助流程](VISITOR_H5.md) |
| 在隔离环境完成电脑和手机联调 | [游客手动验收](VISITOR_MANUAL_TEST.md) |
| 使用双版草稿、配乐、跟踪与精剪 | [景区精剪](SCENIC_EDITING.md) |
| 使用实时单目标识别 | [实时识别](realtime-recognition.md) |
| 独立部署 RTSP、多摄像头配置及热插拔 | [摄像头网关](camera-gateway.md) |
| 将 Windows USB 相机接入 Linux 容器 | [摄像头桥接](windows-camera-bridge.md) |
| 优化 640 / 30fps 预览、配置模型精度和复测 | [相机性能](camera-performance.md) |
| 理解自动采集、录像分段和引用保护 | [录像保留](camera-retention.md) |

## 历史与资源来源

- [实时识别验证记录](realtime-validation.md)、[生产修复记录](production-fixes.md)：保留测试条件、结果和未验收项。
- [模型来源与特征契约](../model/NOTICE.md)、[模型代码许可](../model/LICENSE.CLIP-REID)。
- [内置音乐](../FastAPIBackend/assets/scenic_music/README.md)、[音乐来源记录](../FastAPIBackend/assets/scenic_music/LICENSE_SOURCES.md)。
- [legacy 归档](../legacy/README.md)：Qt/QML 和 pre-v1 API 已冻结，不用于当前启动和发布。

## 文档维护约定

配置以 `FastAPIBackend/app/core/config.py`、各服务读取的环境变量、启动脚本及 Compose 文件为准；接口以实际挂载路由和 `/openapi.json` 为准；依赖及测试入口以锁文件、`package.json` 和 `.github/workflows/ci.yml` 为准。

修改 HTTP method/path 后同步更新 [API.md](API.md)，运行 `python scripts/check_openapi_docs.py`。该检查只比较 HTTP 接口集合，不验证示例字段、权限说明、WebSocket 或部署命令，后者仍须对照源码。新增变量、启动参数或数据目录时同时更新对应专题及入口链接。实测记录写明日期、环境与范围，保留历史证据，不把旧测试数量写成当前总数。
