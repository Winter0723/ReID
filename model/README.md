# 模型文件由使用者自行获取

源码不包含模型权重。应用推理需要以下文件：

| 本地路径 | 上游来源 |
| --- | --- |
| `FastAPIBackend/yolov8n.pt` | [Ultralytics assets](https://github.com/ultralytics/assets/releases)，YOLOv8n 检测权重，遵守 Ultralytics 授权 |
| `model/ViT-MSMT17-60.pth` | [CLIP-ReID](https://github.com/Syliz517/CLIP-ReID) 的 MSMT17 ViT-B-16 checkpoint；同时核对训练数据/权重使用条件 |
| `model/clip_weights/ViT-B-16.pt` | [OpenAI CLIP](https://github.com/openai/CLIP) 官方 ViT-B/16 checkpoint |

兼容版本的 SHA256 与字节数保存在 `checkpoints.json`。取得文件后用
`python scripts/check_models.py` 校验。哈希仅标识兼容版本，不等于授予再分发权；如上游不再
提供该版本，不要从不明镜像下载，更不要绕过校验。模型加载涉及 PyTorch 序列化，只能使用
可信文件。更换权重、预处理、Torch 或特征维数后需重建索引并做样本检索验收。

可通过 `YOLO_MODEL_PATH`、`CLIPREID_MODEL_PATH`、`CLIP_CACHE_DIR` 指向自己的私有位置。
Compose 默认只读挂载上述宿主机路径。执行 `python scripts/init_runtime.py --strict-models`
可检查应用声明的模型路径；它不能确认许可证。
