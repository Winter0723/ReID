# 独立摄像头与 RTSP 网关

网关负责设备发现、持久配置、视频发布和断线恢复，不依赖 MyReID API、数据库、Redis 或向量数据库。其他应用可直接使用标准 RTSP/TCP 读取。数据库、识别、录像和业务点位属于后端。

```text
USB / 本地虚拟摄像头 → 宿主机设备发现 → sources.json → FFmpeg 发布 ┐
网络摄像机 / NVR 通道 ───────────────→ sources.json → RTSP 拉流 ├→ MediaMTX → 任意 RTSP 客户端
                                                            ┘
```

## 独立启动

Windows 需要 Docker Desktop（Linux 容器）、Python 3.11+（含 pythonw.exe）、PATH 中的 FFmpeg（DirectShow/libx264）及桌面应用摄像头权限。Python 模块只使用标准库。

```powershell
.\scripts\run_camera_gateway.ps1
python -m camera_gateway scan
python -m camera_gateway status
# 单独关闭网关并释放摄像头
.\scripts\run_camera_gateway.ps1 -Stop
```

服务使用独立的 `docker-compose.camera-gateway.yml`、Compose 项目 `myreid-camera` 和网络 `myreid-camera`。Windows 设备采集必须在宿主机用户会话运行，Linux Docker 容器无法直接打开 Windows DirectShow 设备。MediaMTX 容器负责协议服务，宿主机模块负责设备与配置，两者都不检查后端是否运行。没有接入任何摄像头也可正常启动。

`run_production.ps1` 会先启动网关，再启动后端项目。`stop_production.ps1` 默认仅停止后端项目；如需全部停止，使用 `stop_production.ps1 -StopCameraGateway`。从旧版本首次启动时会迁移原来的 MediaMTX 容器，保留源地址和凭据，迁移期间会短暂断流。

## 一份配置管理所有源

默认运行目录为 `tmp/camera-bridge/`，可通过绝对路径环境变量 `CAMERA_GATEWAY_DIR` 修改；后端与网关必须使用同一个目录。目录不进入 Git 或镜像构建。

| 文件 | 用途 |
| --- | --- |
| `sources.json` | 用户可编辑的源配置，唯一配置入口 |
| `inventory.json` | 最近一次本地设备发现结果 |
| `registry.json` | 每路源的在线状态及本地编码帧统计 |
| `mediamtx.yml` | 根据 sources 自动生成，不应手改 |
| `credentials.json` | 首次生成的发布、读取、控制密码，重启保留 |
| `agent.log` / `supervisor.log` | 运行和恢复日志 |

本地设备约每 3 秒发现一次；新增物理 USB/PCI/ACPI 摄像头默认写入配置并启用。设备拔出后停止相应发布，重接后复用原 ID、配置与 RTSP 路径，其他路不重启。设备 ID 来自系统设备路径；无序列号设备换 USB 口、驱动重装可能改变路径，需要核对映射。

所有本地视频设备都会被枚举。虚拟摄像头默认登记但禁用，首次选定需要的通道后即自动恢复，不再每次配置。这样空驱动接口不会自动占据监控墙。此规则与厂商品牌无关。

```powershell
python -m camera_gateway enable --id <scan返回的ID>
python -m camera_gateway disable --id <源ID>
python -m camera_gateway probe --id <源ID>
```

配置示例见 [`camera_gateway/sources.example.json`](../camera_gateway/sources.example.json)。网络摄像机或 NVR 的每个通道配置一个源：

```json
{
  "version": 1,
  "discovery": {"auto_enable_physical": true, "scan_interval_seconds": 3},
  "capture": {"width": 640, "height": 480, "fps": 30, "bitrate_kbps": 1500},
  "sources": [
    {"id": "park-gate", "label": "景区入口", "kind": "rtsp", "enabled": true,
     "url": "rtsp://192.168.1.100:554/stream1"}
  ]
}
```

将网络条目加入现有 `sources` 数组，保留已发现的本地条目。也可使用 `python -m camera_gateway add-rtsp --id park-gate --url rtsp://192.168.1.100:554/stream1 --label 入口`。带账号密码的 URL 建议直接写入配置，避免留在命令历史中。上游地址必须能从 MediaMTX 容器访问；容器内 `127.0.0.1` 指向容器自身。

运行时编辑 `enabled`、标签、上游地址或本地 `capture` 参数会自动加载；只调整对应源，不需要重启整个容器。无效 JSON 或参数会记录错误并保留上一份有效运行配置。CLI 配置更新使用文件锁和原子写入；手动编辑后以 `status` 核对结果。

代理同时保存生成配置，并通过 [MediaMTX v1.21.1 控制 API](https://raw.githubusercontent.com/bluenviron/mediamtx/v1.21.1/api/openapi.yaml) 核对、增加、更新和删除对应路径。这样 Windows Docker 挂载未传递文件变更通知时也能生效，容器重启则从保存的配置恢复。

本地源用 H.264 编码，每路可覆盖 `capture`。网络 RTSP 源直接转发原编码，客户端需支持其编码格式。地址/凭据首次配置以后，网络断开、恢复由 MediaMTX 自动重连。当前没有 ONVIF/局域网自动发现：无法仅凭设备入网推断其账号、通道和 RTSP 地址。

## 供其他应用读取

| 客户端位置 | 地址 |
| --- | --- |
| 宿主机 VLC、FFmpeg、OpenCV | `rtsp://reader:<reader密码>@127.0.0.1:18554/local/<源ID>` |
| 加入 `myreid-camera` 网络的其他容器 | `rtsp://reader:<reader密码>@mediamtx:8554/local/<源ID>` |

读取密码在 `credentials.json` 的 `reader` 字段。服务要求认证，默认只向宿主机回环地址开放；需要局域网客户端时，在启动前将 `CAMERA_RTSP_BIND` 设为指定网卡地址并配置对应防火墙规则。控制 API 固定回环 `19997`，不对局域网开放。配置和凭据包含访问密码，不应提交或公开。

仅使用已生成配置的网络 RTSP 源时，可独立运行 `docker compose -f docker-compose.camera-gateway.yml up -d`；运行时修改 `sources.json`、设备发现及状态清单仍由宿主机代理处理。Linux 提供 V4L2 发现适配器，可以依次运行 `python -m camera_gateway init`、Compose、`python -m camera_gateway run`；本次未在 Linux 实际硬件上验收。macOS 暂无本地采集适配器，可使用网络 RTSP 源。

## 状态与画面检验

`online` 表示视频正在提供；`device_offline` 为本地设备已消失，`source_unavailable` 为上游 RTSP 不可用，`gateway_offline` 为 MediaMTX 不可达，`connecting_or_device_busy` 为尚未成功发布，`disabled` 为配置禁用。

`probe` 实际解码最多 3 个样本帧，区分设备接口存在与能否解码。虚拟驱动可能在手机断开时继续输出占位图，因此默认 `signal=unknown`，不能把“能解码”当作“物理传感器在线”，也不把静止场景当断线。

对于有固定断线占位图的本地源，可在明确处于断线占位状态时执行 `python -m camera_gateway learn-placeholder --id <源ID>`。它只将这一源的参考帧存入配置；之后采集进程从同一次采集中抽样，持续匹配时标为 `no_video_signal`，真实画面恢复后自动恢复在线。不要在正常拍摄时学习。此策略不识别任意未知占位图或冻结画面，也不应用于网络直通源的持续状态检测。删除该源的 `health` 项即可取消。

## MyReID 可选集成与验证

后端挂载同一目录并接入网关网络，使用 `MYREID_CAMERA_BRIDGE_DIR` 读取清单和凭据。`camera_hotplug.py` 自动发现在线源、复用已有景区点位及旧 `0` 别名，断开只停止对应入库，重接恢复；人工停用点位与暂停项目会被尊重。`SCENIC_CAMERA_AUTO_START=0` 关闭自动启动。历史点位、录像和数据库记录不会因拔出设备而删除。网关自身不导入这些业务代码。

```powershell
python -m unittest discover -s scripts/tests -p 'test_camera*.py' -v
.\FastAPIBackend\venv\Scripts\python.exe -m pytest FastAPIBackend/tests/test_camera_hotplug.py -q
```

本机验收覆盖 USB 与一个虚拟摄像头的独立 RTSP 解码；实际物理拔插、多台真实 USB、真实 NVR 以及 Linux V4L2 需要在对应硬件上进一步验收。

2026-10-19 独立网关实测：后端容器停止时两路仍可解码；配置禁用/恢复 USB 时另一发布进程持续出帧；运行中增加一条 RTSP 回环拉流源后成功解码 3 帧，移除后路径消失，全程容器启动时间不变。网关单元测试 16 项、后端热插拔及生命周期 23 项、景区发现接口 4 项通过。此记录验证网络源的协议流程，不代替真实 NVR 的网络及编码兼容性验收。
