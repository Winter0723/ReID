# 生产模式交互、检索与摄像头检查

本文保留 2026-09-23 的修复与验收记录，测试数、依赖和“尚未联调”均按当时环境理解。当前启动方式见 [本机生产指南](local-production.md)，后续 USB → RTSP 接入见 [Windows 摄像头桥接](windows-camera-bridge.md)，游客自助流程见 [游客 H5](VISITOR_H5.md)。

## 本次修复

- 基础系统登录窗口和控制面板展示操作进度、成功和错误；景区登录页展示验证码发送反馈，提交期间禁用重复提交。
- 保留邮件接口返回的提示，不再被通用“操作完成”覆盖。认证请求 30 秒超时，其余 JSON 请求默认 120 秒；代理误返回 HTML 时显示配置错误。
- 检索完成立即展示结果，不再等待历史列表。图片、视频通过带签名的受保护 API 返回，避免浏览器访问 Docker 内部 MinIO 地址；视频支持 Range。
- YOLO 和特征提取状态均报告设备。现有 CUDA 推理逻辑已经覆盖检测、图像和文本特征。

## 摄像头获取

1. 创建/选择摄像头项目，在“采集与入库”点击“刷新摄像头列表”。枚举的是后端设备，不是浏览器所在电脑。
2. 可选择后端设备编号，或直接填写 RTSP 地址。即使已有枚举设备，也可以手动输入网络视频流。
3. Windows Docker Desktop 中的 Linux 容器不会自动获得 Windows USB 摄像头。使用摄像头/NVR 提供的 RTSP 地址；Windows 主机上已有的视频流服务可使用容器可访问的主机地址，例如 host.docker.internal。Linux 主机可通过 Compose devices 映射真实 /dev/videoN。
4. 点击启动采集；无法连接时查看页面错误。当前没有把浏览器 getUserMedia 采集上传至后端的功能。

## 应用与核验

在项目根目录运行 `./scripts/run_production.ps1 -Build` 重建并启动生产镜像，然后重启浏览器前端；Tauri 已打包程序需要重新构建。

生产邮件仍需有效 SMTP 配置；前端现在会显示缺失配置、密码错误、验证码过期等服务端反馈，不能代替邮件服务配置。

本机 GPU 验证：RTX 5060 Ti，PyTorch 2.12.0+cu130；YOLO predictor、ReID 图像/文本张量均为 cuda:0，图像特征为有限值的 [1, 512]。2026-09-23 已重建并启动生产容器：容器内 pip check 通过，CUDA 13.0 GPU 运算通过；readyz 返回 ready、device=cuda，CLIP/YOLO、两个数据库、Redis 队列和向量库均就绪。真实邮件投递和真实摄像头流尚未联调。

S3 媒体会先下载到已有 object_storage/cache 再响应，首次读取大视频可能等待完整下载，并占用后端缓存磁盘；后续请求复用缓存。签名鉴权与访问权限检查保留。

## 回归范围

前端生产构建、Vitest；Playwright 在生产构建预览上模拟 API，检查登录失败、发码成功、注册失败和检索缩略图加载，并让历史请求延迟以确认不阻塞结果展示。后端覆盖 S3 媒体同源返回、Range、缺失文件、相机预览和搜索 API。

后端 API 集成测试原有异步入库任务会在测试清理后打印一次外键错误，测试断言通过；这不作为真实生产入库成功的证据。

## Docker 构建依赖修复（2026-09-23）

原 requirements.txt 漏掉 Linux 专用间接依赖，导致 pip --require-hashes 首先报 milvus-lite 未固定版本。已生成带平台条件的 universal 锁，保留原有 111 个版本固定项，补齐 25 个平台依赖及哈希。Linux CI 新增完整依赖解析检查，已验证旧锁失败、新锁通过。

run_production.ps1 -Build 现在只构建 backend 镜像一次，worker 复用同一 myreid:production-gpu 镜像。生产镜像构建及完整启动均已验证成功。
