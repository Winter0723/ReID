# 模型文件由使用者自行获取

源码不包含模型权重。应用推理需要以下文件：

| 本地路径 | 上游来源 |
| --- | --- |
| `FastAPIBackend/yolov8n.pt` | [Ultralytics assets](https://github.com/ultralytics/assets/releases)，YOLOv8n 检测权重，遵守 Ultralytics 授权 |
| `model/ViT-MSMT17-60.pth` | 使用者提供的 DSCL-ReID MSMT17 权重；不能因为适配代码来自 CLIP-ReID 就将权重归属写成 CLIP-ReID |
| `model/Market2MSMT.pth` | 使用者提供的 MGD-DPM teacher checkpoint |
| `model/clip_weights/ViT-B-16.pt` | [OpenAI CLIP](https://github.com/openai/CLIP) 官方 ViT-B/16 checkpoint |

兼容版本的 SHA256 与字节数保存在 `checkpoints.json`。取得文件后用
`python scripts/check_models.py` 校验。哈希仅标识兼容版本，不等于授予再分发权；如上游不再
提供该版本，不要从不明镜像下载，更不要绕过校验。模型加载涉及 PyTorch 序列化，只能使用
可信文件。更换权重、预处理、Torch 或特征维数后需重建索引并做样本检索验收。

可通过 `YOLO_MODEL_PATH`、`CLIPREID_MODEL_PATH`、`CLIP_CACHE_DIR` 指向自己的私有位置。
Compose 默认只读挂载上述宿主机路径。执行 `python scripts/init_runtime.py --strict-models`
可检查应用声明的模型路径；它不能确认许可证。


## 项目多模型

模型目录默认是仓库 `model/`，Docker 为 `/models/reid`。每个权重配一份 `*.model.json`；内置 `clipreid-msmt17`（512 维，图文）和 `mgddpm-market2msmt`（1280 维，仅图片）。目录刷新只检查描述、哈希及 checkpoint 张量结构，不构造模型，也不分配显存。推理时按项目懒加载，默认每进程缓存两个实例；任务和相机持有的实例不会被淘汰。

MGD-DPM 读取 `Market2MSMT.pth` 的 `teacher`，RGB 256×128、bicubic、ImageNet 标准化、stride 12、相机索引 0、SIE 系数 1.0、FP32；保留两路残差 adapter 的训练 alpha，BN 后拼接，原图和水平翻转平均后 L2 归一化。推理实现核对上游提交 `7ff0676817f9dc65eb27d800225670de2a99dfe4` 的 `model/make_model_mgddpm.py` 与 `test_mgddpm.py`。不需要上游训练依赖或下载新的视觉骨干。

DSCL-ReID 默认描述沿用当前应用的 336×160、stride 16、projection CLS 512 维及 auto 精度；这描述的是本应用现有特征提取约定，不宣称等同于 DSCL-ReID 所有官方评测配置。内部 ID `clipreid-msmt17`、适配器键 `clipreid` 和 `CLIPREID_*` 配置名为兼容已有项目暂时保留，显示名称为 DSCL-ReID。已有项目不能继承修改后的描述或被覆盖的权重。文件名、展示名称、目录路径不参与特征空间哈希；权重内容、适配器版本、预处理、输出、维度和推理选项参与。

`python scripts/check_models.py --registry` 查看可用性；真实权重回归可运行 `MYREID_RUN_MODEL_SMOKE=1 python -m pytest FastAPIBackend/tests/test_multi_model_smoke.py`（PowerShell 先设置同名环境变量）。该回归使用隔离测试数据库，覆盖两域入库与自检索，不代表真实游客正负样本的检索效果验收。

这份 DSCL-ReID checkpoint 不含完整 token embedding，因此 `clip_weights/ViT-B-16.pt` 的 SHA256 也计入项目空间。注册模型推理使用目录内这份已固定的基础权重，缺失时明确失败，不自动下载或替换。

### 添加多个 MGD-DPM 权重

把新的 `.pth` 放到 `model/`，为每个权重注册一个不同的 ID 和名称，例如在仓库根目录运行：

```powershell
.\FastAPIBackend\venv\Scripts\python.exe scripts/register_reid_model.py --weights AnotherMGD.pth --id mgddpm-another --name "MGD-DPM Another"
```

脚本校验 teacher 结构后生成独立的 `.model.json`，不会覆盖已有模型。默认沿用当前 Market2MSMT 推理配方；若训练配置不同，显式传入 `--height`、`--width`、`--stride-size`、`--camera-index`、`--sie-coefficient` 或 `--template`。摄像头嵌入数量从权重读取，不再写死为 21。只支持当前 ViT-B/16 teacher 结构；其他结构明确显示不兼容。

随后在项目创建框点击“刷新模型列表”即可选用，无需改代码或重建镜像。只有 `.pth` 而没有配方时显示缺少描述，不能仅凭文件名猜预处理。模型数量不受缓存大小限制：默认缓存两个闲置可淘汰实例，多个长期活动相机需要相应增加资源及缓存容量。相同 1280 维但权重不同的 MGD-DPM 模型仍按 SHA256 分开向量空间。
