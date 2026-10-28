"""File-backed, immutable embedding identities. Discovery never loads a network."""
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.core.config import get_settings

ROOT = Path(__file__).resolve().parents[3]
ADAPTER_VERSIONS = {"clipreid": "1", "mgddpm": "1"}


def model_directory():
    path = Path(get_settings().reid_model_dir).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


@lru_cache(maxsize=64)
def _digest(path, size, mtime, ctime):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_digest(path):
    stat = path.stat()
    return _digest(str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=64)
def _inspect_checkpoint(path, digest, adapter, height, width, stride, camera_index=0):
    """Inspect tensor headers through mmap; do not construct a model or use GPU."""
    import torch
    import numpy as np
    from numpy.core.multiarray import scalar
    try:
        with torch.serialization.safe_globals([(scalar, "numpy.core.multiarray.scalar"), np.dtype,
                                                type(np.dtype("float64")), type(np.dtype("float32"))]):
            data = torch.load(path, map_location="cpu", weights_only=True, mmap=True)
        if adapter == "mgddpm":
            data = data["teacher"]
        state = {key.removeprefix("module."): value for key, value in data.items()}
        expected = {
            "image_encoder.conv1.weight": (768, 3, 16, 16),
            "image_encoder.proj": (768, 512),
            "image_encoder.positional_embedding": (((height - 16) // stride + 1) * ((width - 16) // stride + 1) + 1, 768),
            "image_encoder.class_embedding": (768,),
        }
        for norm in ("ln_pre", "ln_post"):
            for parameter in ("weight", "bias"):
                expected[f"image_encoder.{norm}.{parameter}"] = (768,)
        branches = [("image_encoder.transformer", 768)]
        if adapter == "clipreid":
            branches.append(("text_encoder.transformer", 512))
            expected.update({"text_encoder.positional_embedding": (77, 512),
                             "text_encoder.ln_final.weight": (512,), "text_encoder.ln_final.bias": (512,)})
        for branch, width in branches:
            block = {"attn.in_proj_weight": (3 * width, width), "attn.in_proj_bias": (3 * width,),
                     "attn.out_proj.weight": (width, width), "attn.out_proj.bias": (width,),
                     "ln_1.weight": (width,), "ln_1.bias": (width,), "ln_2.weight": (width,), "ln_2.bias": (width,),
                     "mlp.c_fc.weight": (4 * width, width), "mlp.c_fc.bias": (4 * width,),
                     "mlp.c_proj.weight": (width, 4 * width), "mlp.c_proj.bias": (width,)}
            for index in range(12):
                expected.update({f"{branch}.resblocks.{index}.{key}": shape for key, shape in block.items()})
        if adapter == "mgddpm":
            expected.update({"adapter.fc.0.weight": (192, 768), "adapter.fc.2.weight": (768, 192),
                             "adapter_proj.fc.0.weight": (128, 512), "adapter_proj.fc.2.weight": (512, 128),
                             "adapter.alpha": (), "adapter_proj.alpha": (), "bottleneck.weight": (768,),
                             "bottleneck_proj.weight": (512,)})
            cameras = state.get("cv_embed")
            if (not isinstance(cameras, torch.Tensor) or cameras.ndim != 2
                    or cameras.shape[1] != 768 or cameras.shape[0] <= camera_index):
                raise ValueError("权重结构不匹配: cv_embed 或 camera_index 超出范围")
            expected["cv_embed"] = tuple(cameras.shape)
            for name, width in (("bottleneck", 768), ("bottleneck_proj", 512)):
                expected.update({f"{name}.{key}": (width,) for key in ("bias", "running_mean", "running_var")})
        else:
            expected["text_encoder.text_projection"] = (512, 512)
        for key, shape in expected.items():
            if (key not in state or not isinstance(state[key], torch.Tensor)
                    or state[key].is_meta or tuple(state[key].shape) != shape):
                raise ValueError(f"权重结构不匹配: {key}")
    except Exception as exc:
        raise ValueError(f"无法识别兼容权重: {exc}") from exc


def space_id(snapshot):
    identity = {k: snapshot[k] for k in ("adapter", "adapter_version", "sha256", "preprocess", "output", "dimension", "options")}
    if "base_sha256" in snapshot:
        identity["base_sha256"] = snapshot["base_sha256"]
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read(path):
    return validate_descriptor(json.loads(path.read_text(encoding="utf-8")))


def validate_descriptor(data):
    """Validate a recipe before registration; return a copy with its identity."""
    fields = {"id", "name", "adapter", "adapter_version", "weights", "dimension", "preprocess", "output", "options", "capabilities"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("模型描述字段不完整或包含未知字段")
    data = dict(data)
    if any(not isinstance(data[key], dict) for key in ("preprocess", "options", "capabilities")):
        raise ValueError("预处理、推理选项和能力必须为对象")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise ValueError("模型名称不能为空")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", data["id"]):
        raise ValueError("模型 ID 格式无效")
    adapter = data["adapter"]
    if adapter not in ADAPTER_VERSIONS or data.get("adapter_version") != ADAPTER_VERSIONS[adapter]:
        raise ValueError("不支持的适配器或版本")
    weight = (model_directory() / data["weights"]).resolve()
    if not weight.is_relative_to(model_directory()):
        raise ValueError("权重必须位于模型目录内")
    if not weight.is_file():
        raise ValueError("模型权重文件不存在")
    expected_dimension = 512 if adapter == "clipreid" else 1280
    if data["dimension"] != expected_dimension:
        raise ValueError(f"适配器输出必须为 {expected_dimension} 维")
    if data["capabilities"] != {"image": True, "text": adapter == "clipreid"}:
        raise ValueError("模型能力与适配器不一致")
    # Only explicitly supported recipes may be advertised as available.
    if adapter == "clipreid":
        if data["preprocess"].get("normalization") != "clip" or data["output"] != "projection_cls_l2":
            raise ValueError("DSCL-ReID 预处理或输出配置不兼容")
        allowed = {"model_name", "dataset_name", "num_classes", "camera_num", "view_num", "stride_size", "precision"}
        options = data["options"]
        if options.get("model_name") != "ViT-B-16" or options.get("precision") not in {"auto", "fp32", "fp16", "int8"}:
            raise ValueError("DSCL-ReID 架构或精度不兼容")
        for key in ("num_classes", "camera_num", "view_num", "stride_size"):
            value = options.get(key)
            if type(value) is not int or value < (0 if key == "camera_num" else 1):
                raise ValueError(f"无效的模型配置: {key}")
    else:
        if data["preprocess"].get("normalization") != "imagenet" or data["output"] != "teacher_bn_concat_flip_mean_l2":
            raise ValueError("MGD-DPM 预处理或输出配置不兼容")
        allowed = {"stride_size", "camera_index", "sie_coefficient", "precision"}
        options = data["options"]
        coefficient = options.get("sie_coefficient")
        if (type(options.get("stride_size")) is not int or not 1 <= options["stride_size"] <= 32
                or type(options.get("camera_index")) is not int or options["camera_index"] < 0
                or type(coefficient) not in (int, float) or not math.isfinite(coefficient) or coefficient < 0
                or options.get("precision") != "fp32"):
            raise ValueError("MGD-DPM 推理配置不兼容")
    if (set(data["options"]) != allowed or set(data["preprocess"]) != {"size", "normalization", "interpolation"}
            or data["preprocess"].get("interpolation") != "bicubic"):
        raise ValueError("模型配置字段不完整或不兼容")
    size = data["preprocess"].get("size")
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 16 for v in size):
        raise ValueError("图像尺寸无效")
    data["sha256"] = file_digest(weight)
    _inspect_checkpoint(str(weight), data["sha256"], adapter, *size, data["options"]["stride_size"], data["options"].get("camera_index", 0))
    if adapter == "clipreid":
        # The historical checkpoint does not contain CLIP's token embedding.
        # Pin the local pretrained dependency too; never download it on demand.
        base = model_directory() / "clip_weights" / "ViT-B-16.pt"
        if not base.is_file():
            raise ValueError("缺少 CLIP 基础权重 clip_weights/ViT-B-16.pt")
        data["base_sha256"] = file_digest(base)
    data["embedding_space_id"] = space_id(data)
    return data


def discover_models():
    rows, seen_weights = [], set()
    for path in sorted(model_directory().glob("*.model.json")):
        label = {"id": path.name.removesuffix(".model.json"), "name": path.stem}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if isinstance(raw.get("id"), str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", raw["id"]):
                    label["id"] = raw["id"]
                if isinstance(raw.get("weights"), str):
                    seen_weights.add(raw["weights"])
                if isinstance(raw.get("name"), str):
                    label["name"] = raw["name"]
            data = _read(path)
            seen_weights.add(data["weights"])
            rows.append({**data, "available": True, "unavailable_reason": ""})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows.append({**label, "available": False, "unavailable_reason": str(exc)})
    ids = [row["id"] for row in rows]
    for row in rows:
        if ids.count(row["id"]) > 1:
            row.update(available=False, unavailable_reason="模型 ID 重复")
    for path in sorted(p for p in model_directory().glob("*") if p.suffix.lower() in {".pth", ".pt", ".ckpt", ".safetensors"}):
        if path.name not in seen_weights:
            rows.append({"id": "", "name": path.name, "available": False, "unavailable_reason": "缺少有效的模型描述文件"})
    return rows


def resolve_model(model_id=None):
    model_id = model_id or get_settings().reid_default_model_id
    row = next((r for r in discover_models() if r["id"] == model_id), None)
    if not row or not row["available"]:
        raise HTTPException(409, detail=f"模型不可用: {model_id}: {(row or {}).get('unavailable_reason', '未注册')}")
    return {k: v for k, v in row.items() if k not in {"available", "unavailable_reason"}}


def bind_project(project, model_id=None):
    if getattr(project, "embedding_space_id", None):
        raise HTTPException(409, detail="项目创建后不可更换模型，请新建项目")
    snapshot = resolve_model(model_id)
    project.reid_model_id = snapshot["id"]
    project.embedding_space_id = snapshot["embedding_space_id"]
    project.model_snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def project_snapshot(project, *, verify=True):
    if not project or not project.embedding_space_id:
        raise HTTPException(409, detail="历史项目模型来源待核验；请确认原权重和配置后执行绑定迁移")
    try:
        snapshot = json.loads(project.model_snapshot_json)
        if (snapshot["id"] != project.reid_model_id or space_id(snapshot) != project.embedding_space_id
                or snapshot["embedding_space_id"] != project.embedding_space_id):
            raise ValueError("项目模型快照不一致")
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, detail="项目模型快照损坏") from exc
    if verify:
        current = resolve_model(project.reid_model_id)
        if current["embedding_space_id"] != project.embedding_space_id:
            raise HTTPException(409, detail="项目模型文件或配置已变化，不能继续推理；请恢复原版本")
        # A directory can move between API and worker; identity does not include paths.
        snapshot["weights"] = current["weights"]
    return snapshot


def project_model_info(project):
    info = {"id": project.reid_model_id or "", "embedding_space_id": project.embedding_space_id or "",
            "name": "待核验", "capabilities": {"image": False, "text": False}, "available": False}
    try:
        snapshot = project_snapshot(project, verify=False)
        info.update({k: snapshot[k] for k in ("name", "dimension", "capabilities")})
        project_snapshot(project)
        info.update(available=True, unavailable_reason="")
    except HTTPException as exc:
        info["unavailable_reason"] = exc.detail
    return info


def public_models():
    fields = {"id", "name", "embedding_space_id", "dimension", "capabilities", "available", "unavailable_reason"}
    return [{**{k: v for k, v in row.items() if k in fields},
             "default": row["id"] == get_settings().reid_default_model_id} for row in discover_models()]
