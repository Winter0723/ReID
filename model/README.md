# 模型文件由使用者自行获取

源码不包含模型权重，可通过以下入口下载项目配套模型包：

| 下载方式 | 地址 | 提取码 |
| --- | --- | --- |
| 夸克网盘 | [下载 ReID 模型](https://pan.quark.cn/s/d5cef630d149) | 无 |
| 百度网盘 | [下载 ReID 模型](https://pan.baidu.com/s/1aL_Idla5IHhdmNG5Q7hgbw) | `y26r` |

下载后按下方路径放置文件，并执行校验。以下上游链接用于说明来源；网盘入口不替代各模型原有许可。启动步骤见[根目录上手攻略](../README.md)。

以下路径均相对于源码根目录，模型包也应解压到该目录：

| 本地路径 | 是否必需 | 上游来源 |
| --- | --- | --- |
| `FastAPIBackend/yolov8n.pt` | 必需 | [Ultralytics assets](https://github.com/ultralytics/assets/releases)，YOLOv8n 检测权重，遵守 Ultralytics 授权 |
| `model/ViT-MSMT17-60.pth` | 默认 DSCL-ReID 必需 | 使用者提供的 DSCL-ReID MSMT17 权重；不能因为适配代码来自 CLIP-ReID 就将权重归属写成 CLIP-ReID |
| `model/clip_weights/ViT-B-16.pt` | 默认 DSCL-ReID 必需 | [OpenAI CLIP](https://github.com/openai/CLIP) 官方 ViT-B/16 checkpoint |
| `model/Market2MSMT.pth` | 选择 MGD-DPM 时必需 | 使用者提供的 MGD-DPM teacher checkpoint |

当前 [`checkpoints.json`](checkpoints.json) 记录前三份默认模型文件的 SHA256 与字节数，
不包含可选的 `Market2MSMT.pth`。在源码根目录执行以下命令；这一步只需 Python 3.11+ 标准库：

```sh
python scripts/check_models.py
```

全部显示 `Verified` 才说明三份文件与清单一致。MGD-DPM 的可用性需按下文检查模型注册目录。
哈希仅标识兼容版本，不等于授予再分发权；如上游不再
提供该版本，不要从不明镜像下载，更不要绕过校验。模型加载涉及 PyTorch 序列化，只能使用
可信文件。更换权重、预处理、Torch 或特征维数后需重建索引并做样本检索验收。

## 宿主机路径与 Docker 路径

| 用途 / 配置 | 宿主机默认位置 | Docker 模板值 / 位置 |
| --- | --- | --- |
| 检测权重 `YOLO_MODEL_PATH` | `FastAPIBackend/yolov8n.pt` | `/models/yolov8n.pt` |
| 项目模型目录 `REID_MODEL_DIR` | `model/` | `/models/reid` |
| 默认兼容路径 `CLIPREID_MODEL_PATH` | `model/ViT-MSMT17-60.pth` | `/models/reid/ViT-MSMT17-60.pth` |
| 项目模型的 CLIP 基础权重 | `model/clip_weights/ViT-B-16.pt` | `/models/reid/clip_weights/ViT-B-16.pt` |
| 兼容加载路径的 CLIP 基础权重 | 同上 | 另挂载到 `/workspace/model/clip_weights/ViT-B-16.pt` |

Compose 将整个宿主机 `model/` 只读挂载到 `/models/reid`，因此源码中的
`clipreid-msmt17.model.json`、`mgddpm-market2msmt.model.json` 和后来新增的描述文件都在目录内。
各描述的 `weights` 相对于 `REID_MODEL_DIR`，默认分别是 `ViT-MSMT17-60.pth`、`Market2MSMT.pth`。
注册目录的相对路径以源码根目录解析；在宿主机工具中不要填 `/models/reid` 这样的容器路径。

首次部署保留模板中的 `REID_DEFAULT_MODEL_ID=clipreid-msmt17`、`REID_MODEL_CACHE_SIZE=2`
和空的 `CLIP_CACHE_DIR`。更换目录时同步调整挂载及 `REID_MODEL_DIR`；仅修改
`CLIPREID_MODEL_PATH` 或 `CLIP_CACHE_DIR` 不会修改已注册项目的权重描述及基础权重位置。
`python scripts/init_runtime.py --strict-models` 需要后端依赖，用于检查应用声明的文件路径；
它不会替代模型哈希校验、注册目录检查或许可证确认。

## 项目多模型

模型目录默认是仓库 `model/`，Docker 为 `/models/reid`。每个权重配一份 `*.model.json`；内置 `clipreid-msmt17`（512 维，图文）和 `mgddpm-market2msmt`（1280 维，仅图片）。目录刷新只检查描述、哈希及 checkpoint 张量结构，不构造模型，也不分配显存。推理时按项目懒加载，默认每进程缓存两个实例；任务和相机持有的实例不会被淘汰。

MGD-DPM 读取 `Market2MSMT.pth` 的 `teacher`，RGB 256×128、bicubic、ImageNet 标准化、stride 12、相机索引 0、SIE 系数 1.0、FP32；保留两路残差 adapter 的训练 alpha，BN 后拼接，原图和水平翻转平均后 L2 归一化。推理实现核对上游提交 `7ff0676817f9dc65eb27d800225670de2a99dfe4` 的 `model/make_model_mgddpm.py` 与 `test_mgddpm.py`。不需要上游训练依赖或下载新的视觉骨干。

DSCL-ReID 默认描述沿用当前应用的 336×160、stride 16、projection CLS 512 维及 auto 精度；这描述的是本应用现有特征提取约定，不宣称等同于 DSCL-ReID 所有官方评测配置。内部 ID `clipreid-msmt17`、适配器键 `clipreid` 和 `CLIPREID_*` 配置名为兼容已有项目暂时保留，显示名称为 DSCL-ReID。已有项目不能继承修改后的描述或被覆盖的权重。文件名、展示名称、目录路径不参与特征空间哈希；权重内容、适配器版本、预处理、输出、维度和推理选项参与。

安装后端依赖后，可在源码根目录运行 `python scripts/check_models.py --registry` 查看注册目录的可用性。
宿主机使用 `REID_MODEL_DIR=model` 或实际绝对目录，Docker 使用 `/models/reid`。
这个命令只要有一个模型可用就成功退出，仍需逐项查看所需模型的 `available`；
缺少 `Market2MSMT.pth` 时 MGD-DPM 显示不可用，不影响默认 DSCL-ReID 的选择。

真实权重回归可运行 `MYREID_RUN_MODEL_SMOKE=1 python -m pytest FastAPIBackend/tests/test_multi_model_smoke.py`（PowerShell 先设置同名环境变量）。该回归使用隔离测试数据库，覆盖两域入库与自检索，不代表真实游客正负样本的检索效果验收。

这份 DSCL-ReID checkpoint 不含完整 token embedding，因此 `clip_weights/ViT-B-16.pt` 的 SHA256 也计入项目空间。注册模型推理使用目录内这份已固定的基础权重，缺失时明确失败，不自动下载或替换。

### 添加多个 MGD-DPM 权重

先安装后端依赖，再把新的 `.pth` 放到宿主机 `model/`，为每个权重注册一个不同的 ID 和名称。
例如在仓库根目录用 PowerShell 执行，明确指定本机注册目录：

```powershell
$env:REID_MODEL_DIR = (Resolve-Path .\model).Path
.\FastAPIBackend\venv\Scripts\python.exe scripts/register_reid_model.py --weights AnotherMGD.pth --id mgddpm-another --name "MGD-DPM Another"
.\FastAPIBackend\venv\Scripts\python.exe scripts/check_models.py --registry
```

`--weights` 是相对于 `REID_MODEL_DIR` 的文件名或路径，不能指向目录外。
上面的环境变量只设置在当前 PowerShell 会话中，不会改写 Docker 私有配置。
使用其他宿主机模型目录时，替换 `Resolve-Path` 的路径，并将该目录挂载到各容器的 `/models/reid`。
Docker 挂载是只读的，因此注册脚本在宿主机运行；只使用 Docker 而未安装宿主机后端依赖时，
先使用随源码提供的模型描述，不要在容器中尝试写入注册目录。

脚本校验 teacher 结构后生成独立的 `.model.json`，不会覆盖已有模型。默认沿用当前 Market2MSMT 推理配方；若训练配置不同，显式传入 `--height`、`--width`、`--stride-size`、`--camera-index`、`--sie-coefficient` 或 `--template`。摄像头嵌入数量从权重读取，不再写死为 21。只支持当前 ViT-B/16 teacher 结构；其他结构明确显示不兼容。

随后在项目创建框点击“刷新模型列表”即可选用，无需改代码或重建镜像。只有 `.pth` 而没有配方时显示缺少描述，不能仅凭文件名猜预处理。模型数量不受缓存大小限制：默认缓存两个闲置可淘汰实例，多个长期活动相机需要相应增加资源及缓存容量。相同 1280 维但权重不同的 MGD-DPM 模型仍按 SHA256 分开向量空间。
