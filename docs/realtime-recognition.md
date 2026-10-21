# 实时行人识别

基础工作台侧栏的「实时行人识别」入口提供单目标、单来源识别。支持临时视频、当前项目视频、后端 USB / RTSP。多人参考图点击人物框，未检出时在图上拖动裁剪。结束会话后临时素材删除，下次识别需重新选择素材。

## 运行

- API 只运行 **一个 Uvicorn 进程**，内存中的会话、一次性 ticket 与全局单路限制均为进程级。不要使用多个 workers 或识别过程中热重载。
- 基础依赖包含 `lap==0.5.13`；启动检查缺失依赖并明确报错。
- Windows：`scripts/run_backend.ps1` 使用现有模型设备配置；`scripts/run_backend.ps1 -Realtime` 检查 CUDA 可用后设置 API `MODEL_DEVICE=cuda`。
- 同机 RQ 的 `scripts/run_worker.sh` 和 compose worker 默认 `MODEL_DEVICE=cpu`，独立机器可用 `WORKER_MODEL_DEVICE` 覆盖。
- 新环境可运行 `scripts/install_gpu.ps1` 安装并检查 CUDA，再设置 `MODEL_DEVICE=cuda`。RTX 5060 Ti 的历史推理结果与本机生产模式见 [local-production.md](local-production.md)；该 Compose 组合让 API 与单个 RQ Worker 使用 GPU，并启用 NVENC，与基础 Compose 的 worker 默认值不同。
- Windows 直接采集模式默认优先选择名称含 USB 的设备，排除 Iriun；启动时再次校验名称。生产模式读取独立网关清单，统一接入物理、显式启用的虚拟和网络摄像头，见 [摄像头与 RTSP 网关](camera-gateway.md)。实时页面枚举名称时不打开摄像头。未接入设备时不会自动开始采集。外部 RTSP 需手动填写。

## 配置

| 环境变量 | 初始值 | 含义 |
| --- | --- | --- |
| `REALTIME_ENTER_THRESHOLD` | `0.65` | 新目标进入阈值 |
| `REALTIME_EXIT_THRESHOLD` | `0.55` | 已确认目标保留阈值 |
| `REALTIME_MARGIN` | `0.08` | 第一、第二名最小分差 |
| `REALTIME_ORIGINS` | localhost:5173 与 Tauri 默认来源 | 允许建立 WebSocket 的 Origin，逗号分隔，必须含协议与端口 |

阈值是待校准的初始配置，不能视为身份概率或准确率保证。前三次合格的**新特征测量**才确认；稳定轨迹每 0.5 源时间秒复核，出现新轨迹、丢失后重现或交叉时立即复核。参考特征固定，批量最多 16 张裁剪，低分检测保留至 0.1。ByteTrack 的高低分分组保留原检测索引，画框使用当前检测位置。无当前检测时不输出旧框。

## 输出与资源行为

相机默认等比例缩至 640 宽，识别目标帧率默认 30 FPS（`REALTIME_CAMERA_INFERENCE_FPS`）；实际取决于相机、人数、模型和网络。文件画面缩至不超过 1280×720，按顺序解码、采样不超过 10 FPS，慢处理放慢播放；低帧率视频保持源帧率。单个 WebSocket JSON 消息包含 JPEG base64、会话/控制版本、帧序号、源时间、尺寸、目标状态、检测置信度、ReID 分数和处理耗时。基础相机预览和景区监控墙独立使用 MJPEG，详见 [相机性能](camera-performance.md)。

客户端完整解码后提交画面与该帧元数据并 ACK。每条连接最多一帧等待 ACK，后端仅保留最新待发帧。视频等待 ACK 才继续，摄像头继续更新最新帧。10 秒未 ACK 会断开，断连 30 秒后停止。页面切换、项目切换、认证变化、停止时释放自己的会话；异步请求或解码结果通过版本号废弃。

相机使用 `acquire(record=False)` / `release(record=False)`，不创建录像、索引或业务记录。原有录制消费者继续正常运行。仅实时页面使用时，不写相机预览文件。相机暂停恢复会丢弃旧缓存并重新确认；较大时间间隔重置轨迹。

临时素材在 `FastAPIBackend/.tmp/realtime`，不置于公开静态目录。单用户最多 8 个上传素材，图片 20 MiB / 2500 万像素、视频 1 GiB。会话结束删除其素材，未使用素材与进程重启遗留文件一小时后清理。会话终态保留一分钟供状态查询。

## API

前缀 `/api/v1/projects/{project_id}/realtime`。REST 使用基础 JWT、项目写权限，会话和临时素材还检查所属用户。

| 方法 / 路径 | 用途 |
| --- | --- |
| `GET /cameras` | 无取帧的设备名称枚举；生产模式使用通用网关清单 |
| `POST /inputs?kind=image或video` | 临时 multipart 上传；图片返回人物框和预览 |
| `DELETE /inputs/{id}` | 删除未占用素材 |
| `GET /media/{id}/preview` | 读取项目视频首帧，不触发离线入库 |
| `POST /sessions` | `reference_id`、归一化 `crop`、`source`、`show_others` |
| `GET /sessions/{id}` | 状态、错误和最近帧指标 |
| `POST /sessions/{id}/control` | `action: pause/resume/stop` |
| `POST /sessions/{id}/stream-ticket` | 30 秒一次性、会话绑定凭据 |
| `WS /sessions/{id}/stream?ticket=...` | 同步帧，收到后返回 `{ "ack": sequence }`；状态心跳返回 `{ "pong": true }` |

`source` 只能是 `{media_id}`、`{input_id}`、`{camera}` 之一。流不携带长期访问令牌。连接校验配置的 Origin、凭据过期/重放、所属项目及连接时用户权限。每会话只允许一个连接。自定义浏览器域名需加入后端 `REALTIME_ORIGINS`；Tauri 包装器在客户端 CSP 中添加实际后端的 ws/wss 地址，两项配置分别控制服务端来源校验和客户端连接权限。

## 可复现素材验证

`FastAPIBackend/venv/Scripts/python.exe scripts/benchmark_realtime.py --reference <单人裁剪图> --video <视频> --market-root <Market-1501目录> --output <指标JSON>`

该脚本只读素材，不操作业务表或向量库，运行真实会话处理链路并记录逐帧指标；Market 部分选取 24 个身份的跨相机图对。视频需要人工逐帧身份标注才能计算误匹配率。小样本阈值统计不是独立验证集上的准确率。

自动化替身不能代替 USB / RTSP 实机验收。早期记录见 [实时识别验证](realtime-validation.md)，后续 Windows USB → RTSP 桥接及硬件验收见 [Windows 摄像头接入](windows-camera-bridge.md)。新部署仍需分别验证设备取流、断线恢复和打包桌面端的真实 WebSocket 连接。

## Windows 启动后直接退出 / WinError 10013

若 Windows 的 TCP 排除端口范围覆盖 2889，可在根目录 `.env.local` 设置 `MYREID_API_PORT=12889`（或其他可绑定端口），然后重启前后端。`scripts/run_backend.cmd`、Vite 代理和 Tauri 开发配置共用该值；不需要修改系统保留端口。本机生产组合也默认使用 12889。

后端在加载模型前检查端口；`-Port` 可用于临时诊断。程序内执行数据库迁移时保留 API / Uvicorn 日志，绑定失败等错误会正常显示。前端仍使用原启动入口 `scripts/run_tauri_frontend.cmd`；该入口现在通过生成实际后端 HTTP/WebSocket 权限的 `tauri:dev` 命令启动。
