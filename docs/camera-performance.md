# 相机流畅度与模型精度

基础系统相机预览、景区监控墙和登记抓拍预览使用共享 MJPEG 流。每个物理源只采集、编码一次；观看者读取内存中的最新 JPEG，慢连接跳过旧帧。推理、入库和状态轮询不会决定预览刷新频率。图片快照仅作兼容回退，每秒更新一次；纯实时识别消费者不保存快照或录像。

## 默认值与画质

| 配置 | 默认 | 用途 |
| --- | --- | --- |
| `CAMERA_CAPTURE_WIDTH` | 640 | 本地设备协商宽度；源不接受时，后端按此宽度等比例缩小采集帧 |
| `CAMERA_CAPTURE_HEIGHT` | 480 | 本地设备协商高度，不强行拉伸画面 |
| `CAMERA_PREVIEW_WIDTH` | 640 | JPEG 预览最大宽度 |
| `CAMERA_PREVIEW_FPS` | 30 | 设备请求帧率及预览编码目标 |
| `CAMERA_JPEG_QUALITY` | 75 | 预览 JPEG 质量 |
| `REALTIME_CAMERA_INFERENCE_FPS` | 30 | 实时单目标识别的相机处理上限；视频文件仍采样 10fps |
| `YOLO_IMAGE_SIZE` | 640 | 检测输入尺寸，32 的倍数 |
| `YOLO_PRECISION` | auto | CUDA 使用 FP16；CPU/MPS 使用 FP32，可明确选择 fp32/fp16 |
| `CLIPREID_PRECISION` | auto | 同上，另支持显式选择 CPU int8 |

640 指宽度，不是 640×640 拉伸。16:9 相机得到 640×360，4:3 相机得到 640×480。采集帧也供录像和行人裁剪使用，因此默认保存的录像、远处行人的细节都会减少。需要高清存档时提高 `CAMERA_CAPTURE_WIDTH/HEIGHT`，保持 `CAMERA_PREVIEW_WIDTH=640`，再复测负载。Windows 桥接目前固定输出最多 640×480；要恢复桥接高清存档，还需调整桥接输出尺寸。

USB 驱动不一定支持原生 640。桥接会查询可用模式，优先接近 640 / 30fps；只有高清模式时先采集再缩放。网络摄像头建议在设备上配置 640 / 30fps 子码流；仅后端缩放不会减少网络接收和解码成本。不支持 30fps 的设备不能通过重复帧得到真实 30fps。

## 更新与检查

新默认值随代码生效，已有显式环境变量仍优先。更新前端构建、重启后端与使用模型的 worker；Windows + Docker 部署还须重启相机桥接进程，使其不再输出旧的 20fps。使用现有部署流程，不需要数据库迁移；修改精度后需重新加载模型。不要在旧镜像上仅修改环境变量期待出现新功能。

MJPEG 要求反向代理关闭缓冲，仓库 Nginx 已配置 `proxy_buffering off`，接口也返回 `X-Accel-Buffering: no`。API 仍采用单 Uvicorn 进程，共享相机注册表属于进程内状态。多进程部署需另建独立采集服务。

多路监控要考虑浏览器 HTTP/1.1 同源连接数和 JPEG 带宽；大量摄像头应通过支持 HTTP/2 的入口或另行接入 WebRTC 等视频分发方式。当前实测为两路共享预览，不代表几十路监控已通过验收。

前端先凭项目权限获取短期、路径绑定的预览 URL，接近过期时更新，出错后重连；卸载组件即关闭连接。流本身不启动、续租或录制相机，所以停止入库后不会被观看者重新启动。登记预览仍使用原有 15 秒租约管理采集。

基础状态的 `monitor_status` 包含 `capture_fps`、`preview_fps`、`target_fps`、`input_width/height` 和处理后 `width/height`。景区点位墙也返回采集和预览源帧率。界面的“预览源 FPS”是后端编码速率，不是浏览器显示速率。30fps 预览不等于 YOLO + ReID 对每一帧完成识别；实时识别页的处理耗时、FPS 才反映识别链路。

## 本机测量（2026-10-19）

2026-10-20 补测：Windows 本机 USB、640×360、10 秒、两个 MJPEG 读取者，采集/编码约 **29.8fps**，两个读取者各 **29.87fps**（299 帧，每路约 8.78Mbps）。这次同样未启用录像和推理，也未经过网关、Docker 和远程网络，不能将其等同于全部页面或识别处理达到 30fps。

运营总览和跨模态检索已接入连续预览；流正常播放时停止重复下载快照，失败时恢复快照。采集循环移除了设备读取之后多余的定时等待。实时采集列表展示全部已授权来源，支持手动刷新及每 5 秒更新，不再按 Iriun 名称排除设备或强制优先 USB。

相机项目默认随 API 启动恢复，可用 `CORE_CAMERA_AUTO_START=0` 关闭；景区仍由 `SCENIC_CAMERA_AUTO_START` 控制。数据库和向量索引就绪后即启动相机调度，与模型加载并行；模型加载失败仍执行相机清理。两种项目每 3 秒重试未成功启动的来源，成功启动后尊重本进程中的手动停止。`CAMERA_AUTO_START` 仍仅控制独立默认相机，不需要为了项目自动启动而打开它。Windows Docker 部署应使用 `scripts/run_production.ps1` 同时启动宿主摄像头网关，单独启动容器无法启动宿主设备采集进程。

新建景区同步初始化游客自助服务；API 启动时为历史景区补齐缺失的配置，保留运营人员已保存的关闭状态和价格。

Windows USB Camera 只公布 1920×1080、30fps 模式。使用真实摄像头，缩至 640×360，两个同时读取的 MJPEG 生成器，连续 12 秒：采集与编码约 **29.8fps**，两个读取者分别 **29.75fps**。每路约 7.01Mbps，复杂画面会增大 JPEG 带宽。测试不写录像、不运行识别，也未经过 Docker RTSP、代理和真实网络，因此不是生产全链路的 30fps 保证。

另对同一摄像头进行 FFmpeg DirectShow 采集、缩放及 H.264 编码短测：5 秒输出 150 帧，其中 26 帧为 FFmpeg 补帧，非重复帧约 **25fps**。因此桥接设置 `-r 30` 并不能证明真实达到 30fps；生产桥接全链路仍待复测。桥接注册表和设备接口现在提供 `output_fps`、`nonduplicate_fps`、累计 `dup_frames` 与 `drop_frames`，可用 `python scripts/camera_bridge.py status` 检查。前两项按进度报告间隔及墙钟时间计算，非重复帧率是编码输出估算，不等于传感器采集率，也无法识别设备自身重复的画面；离线时速率清零。

另用 Chromium 在基础页面、景区登记和监控墙验证真实 HTTP multipart 流解码、像素持续变化、状态轮询不重建连接、切页关闭连接；画面使用合成 JPEG。Browser 插件未提供，采用仓库 Playwright 流程，1120×720。桌面 WebView 和移动端未在这轮实测。

真实权重的合成输入小样本结果如下；CUDA 为 RTX 5060 Ti，CPU 固定 4 个 PyTorch 线程。每项热身 3 次，CUDA 测 10 次，CPU 测 5 次，计入预处理。不同运行时负载会改变结果。

| 模式 | ViT 单图中位耗时 | YOLO 中位耗时 | 单图特征与 FP32 余弦相似度 |
| --- | --- | --- | --- |
| CUDA FP32 | 6.81ms | 4.90ms | 1.000000 |
| CUDA FP16 | 7.50ms | 5.00ms | 0.999998 |
| CPU FP32 | 154.27ms | 33.28ms | 1.000000 |
| CPU ViT INT8，YOLO FP32 | 82.48ms | 26.84ms | 0.907973 |

这个样本里 GPU FP16 **没有显示出单图延迟优势**，但提供了低精度执行和较低权重显存占用的选择。CPU INT8 加快了 ViT，却有明显特征漂移，默认不启用。上述相似度不是 ReID 准确率，YOLO 的 CPU 两次结果也不能归因于 ViT 量化。

复现命令（仓库根目录，Linux 将解释器替换为对应 Python）：

```powershell
FastAPIBackend/venv/Scripts/python.exe scripts/benchmark_camera_preview.py --seconds 12 --viewers 2 --output tmp/camera-live-benchmark.json
FastAPIBackend/venv/Scripts/python.exe scripts/benchmark_models.py --device cuda --precision fp16 --iterations 20 --output tmp/models-fp16.json
FastAPIBackend/venv/Scripts/python.exe scripts/benchmark_models.py --device cpu --precision int8 --iterations 20 --image path/to/person-crop.jpg --output tmp/models-int8.json
```

相机脚本读取设备但不保存画面或录像，测试前应确保设备未被其他进程独占。模型脚本未提供 `--image` 时使用合成图片，仅用于执行与数值检查。

## INT8 使用边界

ViT 的 `MODEL_DEVICE=cpu` + `CLIPREID_PRECISION=int8` 对 Transformer MLP 的 Linear 层执行动态 INT8；注意力、卷积和归一化仍为浮点，输出仍为归一化 FP32 512 维特征。FP16 模式保留 CLIP 自定义 LayerNorm 的 FP32 计算。当前实现使用已安装 PyTorch 的 `torch.ao.quantization` API，测试通过，但此 API 有弃用警告，后续升级需迁移并重新验证。

在真实跨相机行人验证集上比较 rank-1、mAP、误匹配率及阈值后，才应启用 INT8。图库、查询和 worker 必须使用同一精度配置；切换 INT8 后应重新提取图库特征，不能仅凭维度相同混用旧向量。

YOLO 是卷积检测器，不能把 Linear 动态量化当成完整 INT8。提供校准导出脚本，支持 CPU OpenVINO 和 NVIDIA TensorRT：

```powershell
FastAPIBackend/venv/Scripts/python.exe scripts/export_yolo_int8.py --model FastAPIBackend/yolov8n.pt --data path/to/calibration.yaml --format openvino --device cpu
# 在具备 TensorRT 的 CUDA 环境中：
FastAPIBackend/venv/Scripts/python.exe scripts/export_yolo_int8.py --model FastAPIBackend/yolov8n.pt --data path/to/calibration.yaml --format engine --device 0
```

导出环境须安装对应 OpenVINO/NNCF 或 TensorRT 依赖，运行环境也须具备对应推理库；基础 requirements 不自动增加这些可选依赖。脚本针对仓库锁定的 Ultralytics 8.4.60，使用其 `int8=True` 导出参数。校准 YAML 应引用实际景区光照、距离、密度等有代表性的图像，使用独立标注集验证检测召回率。通过后将 `YOLO_MODEL_PATH` 指向导出的目录或 `.engine`，并选择相应 `MODEL_DEVICE`。脚本不会修改部署配置或原始 `.pt` 权重。

本轮未提供代表性校准数据，**没有导出或启用 YOLO INT8 模型**。配置与导出入口已经具备，不能据此声称 INT8 精度验收完成。原始权重保持不变。

参考：[Ultralytics 导出](https://docs.ultralytics.com/modes/export/)、[PyTorch 动态量化](https://docs.pytorch.org/docs/main/generated/torch.ao.quantization.quantize_dynamic.html)。
