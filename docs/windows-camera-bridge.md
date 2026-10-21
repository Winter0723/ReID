# Windows 摄像头接入

当前使用通用、独立的 [摄像头与 RTSP 网关](camera-gateway.md)。设备发现、配置、采集和 RTSP 服务不依赖后端；多个 USB、已选择的虚拟摄像头、网络摄像机及 NVR 通道统一管理。

```powershell
.\scripts\run_camera_gateway.ps1
python -m camera_gateway scan
python -m camera_gateway status
# 虚拟设备首次选择；物理设备默认自动启用
python -m camera_gateway enable --id <设备ID>
# 单独停止并释放摄像头
.\scripts\run_camera_gateway.ps1 -Stop
```

配置入口为 `tmp/camera-bridge/sources.json`。后续接入、断开、恢复和配置变更自动处理，无需每次重启或改代码。旧 `scripts/camera_bridge.py` 是通用模块的兼容入口；旧 `enable-iriun` 命令已被按设备 ID 的 `enable` 取代。首次升级会保留旧版已选择的虚拟通道。

完整生产栈仍用 `run_production.ps1` 启动。停止后端默认保留独立网关；全部关闭用 `stop_production.ps1 -StopCameraGateway`。硬件、配置格式、第三方读取、状态判断和限制见 [网关文档](camera-gateway.md)，编码参数见 [相机性能](camera-performance.md)。

以下为历史版本验收记录，其中跟随后端启停、枚举间隔等行为不再代表当前网关。

## 本机验收记录（2026-09-23）

- `run_production.ps1 -Build` 已成功重建、启动生产后端及 worker；`readyz` 返回 ready、cuda、Redis 队列、双数据库与向量库就绪，无 degraded reasons。
- 核心、景区、实时识别三个摄像头发现接口均返回在线 `USB Camera`。
- RTSP 连续解码 30 帧，1280×720；默认源 `0` 正确解析到稳定 USB 流。
- 项目 4（USB 摄像头生产验收）预览帧序号从 6 增至 56、96；预览 JPEG 为 71,907 字节。
- 实际检测入库 1 个目标，摄像头任务状态 succeeded，无 error。
- 媒体 57：录像大小 10,870,700 字节，经正式内容接口从 MinIO 下载，OpenCV 成功解码。
- 强制终止采集代理后守护进程自动恢复；RTSP 容器重启后自动恢复，最终仅一个 FFmpeg 发布进程。
- `run_camera_bridge.ps1 -Stop` 后再次启动成功，USB Camera 恢复 online。
- 宿主机桥接单元测试 4 项通过；隔离容器内摄像头/实时识别回归测试 27 项通过。
- 没有实际拔插 USB 或重启 Windows；这两项不作为已实测结果。设备重枚举和重新连接逻辑已实现；后续已取消登录自动采集，改为跟随生产项目启停。

验收项目 2、3 是修复过程中保留的测试记录；项目 4 是完整通过的最终验收记录。

## 启停联动验收

已实测在 Docker 中直接停止 mediamtx：设备状态变为 project_stopped，宿主机 FFmpeg 发布进程数降到 0；重新启动 mediamtx 后恢复 online。
完整启停入口为 `scripts/run_production.ps1` 与 `scripts/stop_production.ps1`。

### 景区本机摄像头数量同步

景区点位列表、相机墙及启动监控墙现在按桥接代理的在线设备筛选本机源；旧编号 `0` 与对应 RTSP 地址按同一设备去重。不存在的数字编号（例如旧环境的 `3`）不会进入相机墙，重复同步不会再创建 RTSP 副本。外部手动配置的 RTSP 摄像头继续保留。
没有在线本机设备时，不创建默认占位摄像头；既有点位和历史录像不会被删除。

## USB + Iriun 本机验收（2026-10-19）

- 显式启用 `Iriun Webcam`；未启用的 `Iriun Webcam #2` 和 OBS 不参与采集。USB 默认源保持不变。
- 已在现有景区项目 1 添加点位 3“游玩区 · iPhone（Iriun）”，原 USB 入口点位 1 保留。两次发现及页面同步均没有生成重复点位。
- 两路 RTSP 同时连续解码各 60 帧：USB 640×360，Iriun 640×480；两路预览帧版本持续更新，并分别检测入库。
- 两路已封存录像经正式内容接口下载并成功解码：媒体 1818（USB）与 1819（Iriun）。
- 桥接单元测试 9 项通过；Playwright 在 1440×1000 桌面页面验证两路实际画面及同步操作，无空白页或框架错误遮罩。
- 当前运行的既有生产镜像尚无 `preview-stream` 接口，页面两路均回退到逐帧 JPEG 预览，对应 404 是已知版本限制。尝试整体更新时发现本地游客端生产配置要求与既有环境不同，已恢复原镜像，未修改游客配置；实时 MJPEG 接口升级不作为本次完成项。
- 已验证采集、入库与录像回放；未验证手机断线恢复、长时间稳定性和跨镜识别准确率。
