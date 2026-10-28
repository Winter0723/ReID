from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import sys
from threading import Lock
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

from app.core.config import ensure_external_tool_env, get_settings
from app.services.model_precision import prepare_clipreid, resolve_precision


ensure_external_tool_env()

from ultralytics import YOLO


def _resolve_device() -> torch.device:
    configured = get_settings().model_device.strip().lower()
    if configured and configured != "auto":
        return torch.device(configured)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


DEVICE = _resolve_device()


@dataclass(frozen=True)
class Detection:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float = 0.0
    class_id: int = 0
    label: str = "person"

    def clamped_xyxy(self, width: int, height: int) -> Tuple[int, int, int, int]:
        x1 = max(0, min(width - 1, int(self.x1)))
        y1 = max(0, min(height - 1, int(self.y1)))
        x2 = max(0, min(width, int(self.x2)))
        y2 = max(0, min(height, int(self.y2)))
        return x1, y1, x2, y2

    def crop_from(self, frame):
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = self.clamped_xyxy(width, height)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]


class EmbeddingService(ABC):
    """Image/text embedding boundary for ReID indexing and retrieval."""

    name = "embedding"
    backend = "unknown"

    @abstractmethod
    def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def embed_image(self, image: Image.Image) -> torch.Tensor:
        raise NotImplementedError

    @abstractmethod
    def embed_text(self, text: str) -> torch.Tensor:
        raise NotImplementedError

    @abstractmethod
    def is_loaded(self) -> bool:
        raise NotImplementedError

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "backend": self.backend,
            "loaded": self.is_loaded(),
            "dimension": get_settings().embedding_dimension,
            "device": str(DEVICE),
        }


class DetectionService(ABC):
    """Person detection boundary for image, video, and camera ingestion."""

    name = "detection"
    backend = "unknown"

    @abstractmethod
    def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def detect_people(self, frame, *, min_confidence: float = 0.0) -> List[Detection]:
        raise NotImplementedError

    @abstractmethod
    def is_loaded(self) -> bool:
        raise NotImplementedError

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "backend": self.backend,
            "loaded": self.is_loaded(),
        }


class ReIDModelService(ABC):
    """Composes embedding and detection services behind one swappable facade."""

    @abstractmethod
    def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def embed_image(self, image: Image.Image) -> torch.Tensor:
        raise NotImplementedError

    @abstractmethod
    def embed_text(self, text: str) -> torch.Tensor:
        raise NotImplementedError

    @abstractmethod
    def detect_people(self, frame, *, min_confidence: float = 0.0) -> List[Detection]:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        raise NotImplementedError


class CLIPEmbeddingService(EmbeddingService):
    name = "CLIP embedding baseline"
    backend = "clip"

    def __init__(self) -> None:
        self._model: Optional[CLIPModel] = None
        self._processor: Optional[CLIPProcessor] = None
        self._lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is not None and self._processor is not None:
                return
            settings = get_settings()
            model = CLIPModel.from_pretrained(settings.clip_model_id).to(DEVICE)
            if DEVICE.type in {"cpu", "mps"}:
                model.float()
            processor = CLIPProcessor.from_pretrained(settings.clip_model_id)
            model.eval()
            self._model = model
            self._processor = processor

    def embed_image(self, image: Image.Image) -> torch.Tensor:
        self.load()
        with self._inference_lock, torch.inference_mode():
            inputs = self._processor(images=image.convert("RGB"), return_tensors="pt")
            vision_outputs = self._model.vision_model(pixel_values=inputs["pixel_values"].to(DEVICE))
            return F.normalize(self._model.visual_projection(vision_outputs.pooler_output), p=2, dim=-1)

    def embed_images(self, images: List[Image.Image]) -> torch.Tensor:
        self.load()
        with self._inference_lock, torch.inference_mode():
            inputs = self._processor(images=[image.convert("RGB") for image in images], return_tensors="pt")
            outputs = self._model.vision_model(pixel_values=inputs["pixel_values"].to(DEVICE))
            return F.normalize(self._model.visual_projection(outputs.pooler_output), p=2, dim=-1)

    def embed_text(self, text: str) -> torch.Tensor:
        self.load()
        with self._inference_lock, torch.inference_mode():
            inputs = self._processor(text=[text], return_tensors="pt", padding=True, truncation=True)
            text_outputs = self._model.text_model(
                input_ids=inputs["input_ids"].to(DEVICE),
                attention_mask=inputs["attention_mask"].to(DEVICE),
            )
            return F.normalize(self._model.text_projection(text_outputs.pooler_output), p=2, dim=-1)

    def is_loaded(self) -> bool:
        return self._model is not None and self._processor is not None

    def status(self) -> Dict[str, Any]:
        return {
            **super().status(),
            "model_id": get_settings().clip_model_id,
            "device": str(DEVICE),
        }


class CLIPReIDEmbeddingService(EmbeddingService):
    name = "DSCL-ReID MSMT17 embedding"
    backend = "clipreid"

    def __init__(self, settings=None, base_model_path=None) -> None:
        self._settings = settings or get_settings()
        self._base_model_path = base_model_path
        self._model = None
        self._preprocess = None
        self._tokenize = None
        self._model_path: Optional[Path] = None
        self._lock = Lock()
        self._inference_lock = Lock()
        self._image_input_dtype = torch.float32
        self._precision = "unloaded"

    def load(self) -> None:
        with self._lock:
            if self._model is not None and self._preprocess is not None and self._tokenize is not None:
                return
            settings = self._settings
            model_path = self._resolve_model_path(settings.clipreid_model_path or settings.custom_reid_model_path)
            if not model_path.exists():
                raise RuntimeError(f"CLIP-ReID checkpoint not found: {model_path}")

            self._ensure_project_root_importable()
            from model.make_model_clipreid import make_model
            from model.clip import clip
            from torchvision.transforms import Compose, Normalize, Resize, ToTensor
            from torchvision.transforms import InterpolationMode

            cfg = self._build_cfg(settings)
            cfg.MODEL.BASE_WEIGHTS = self._base_model_path
            model = make_model(cfg, settings.clipreid_num_classes, settings.clipreid_camera_num, settings.clipreid_view_num)
            model.load_param(str(model_path))
            model.to(DEVICE)
            self._precision = resolve_precision(settings.clipreid_precision, DEVICE)
            model = prepare_clipreid(model, self._precision)

            self._model = model
            self._tokenize = clip.tokenize
            self._preprocess = Compose([
                Resize((settings.clipreid_image_height, settings.clipreid_image_width), interpolation=InterpolationMode.BICUBIC),
                ToTensor(),
                Normalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711)),
            ])
            self._model_path = model_path
            self._image_input_dtype = next(model.image_encoder.parameters()).dtype

    def embed_image(self, image: Image.Image) -> torch.Tensor:
        self.load()
        with self._inference_lock, torch.inference_mode():
            inputs = self._preprocess(image.convert("RGB")).unsqueeze(0).to(
                device=DEVICE,
                dtype=self._image_input_dtype,
            )
            features = self._model(inputs, get_image=True)
            return F.normalize(self._feature_tensor(features), p=2, dim=-1)

    def embed_images(self, images: List[Image.Image]) -> torch.Tensor:
        self.load()
        with self._inference_lock, torch.inference_mode():
            inputs = torch.stack([self._preprocess(image.convert("RGB")) for image in images]).to(
                device=DEVICE, dtype=self._image_input_dtype)
            return F.normalize(self._feature_tensor(self._model(inputs, get_image=True)), p=2, dim=-1)

    def embed_text(self, text: str) -> torch.Tensor:
        self.load()
        with self._inference_lock, torch.inference_mode():
            tokens = self._tokenize([text], truncate=True)
            if not hasattr(self._model, "encode_text_tokens"):
                raise RuntimeError("CLIP-ReID model does not expose encode_text_tokens")
            features = self._model.encode_text_tokens(tokens)
            return F.normalize(self._feature_tensor(features), p=2, dim=-1)

    def is_loaded(self) -> bool:
        return self._model is not None and self._preprocess is not None and self._tokenize is not None

    def status(self) -> Dict[str, Any]:
        settings = self._settings
        return {
            **super().status(),
            "model_path": str(self._model_path or self._resolve_model_path(settings.clipreid_model_path or settings.custom_reid_model_path)),
            "model_name": settings.clipreid_model_name,
            "dataset": settings.clipreid_dataset_name,
            "image_size": [settings.clipreid_image_height, settings.clipreid_image_width],
            "precision": self._precision,
            "device": str(DEVICE),
        }

    @staticmethod
    def _build_cfg(settings):
        stride = [settings.clipreid_stride_size, settings.clipreid_stride_size]
        return SimpleNamespace(
            MODEL=SimpleNamespace(
                NAME=settings.clipreid_model_name,
                COS_LAYER=False,
                NECK="bnneck",
                SIE_COE=3.0,
                SIE_CAMERA=settings.clipreid_camera_num > 0,
                SIE_VIEW=False,
                STRIDE_SIZE=stride,
                DEVICE=str(DEVICE),
            ),
            TEST=SimpleNamespace(NECK_FEAT="after"),
            INPUT=SimpleNamespace(SIZE_TRAIN=[settings.clipreid_image_height, settings.clipreid_image_width]),
            DATASETS=SimpleNamespace(NAMES=settings.clipreid_dataset_name),
        )

    @staticmethod
    def _feature_tensor(features) -> torch.Tensor:
        if isinstance(features, torch.Tensor):
            tensor = features
        elif isinstance(features, (list, tuple)) and features:
            tensor = CLIPReIDEmbeddingService._feature_tensor(features[0])
        else:
            raise RuntimeError(f"Unsupported CLIP-ReID feature output: {type(features).__name__}")
        return tensor.float().reshape(tensor.shape[0], -1)

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _ensure_project_root_importable(cls) -> None:
        root = str(cls._project_root())
        if root not in sys.path:
            sys.path.insert(0, root)

    @classmethod
    def _resolve_model_path(cls, configured_path: str) -> Path:
        raw = Path(configured_path or "model/ViT-MSMT17-60.pth").expanduser()
        if raw.is_absolute():
            return raw
        root = cls._project_root()
        backend_root = root / "FastAPIBackend"
        for candidate in (root / raw, backend_root / raw, Path.cwd() / raw):
            if candidate.exists():
                return candidate.resolve()
        return (root / raw).resolve()


class YOLODetectionService(DetectionService):
    name = "YOLO person detector"
    backend = "yolo"

    def __init__(self) -> None:
        self._model: Optional[YOLO] = None
        self._lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._lock:
            if self._model is None:
                self._model = YOLO(get_settings().yolo_model_path, task="detect")

    def detect_people(self, frame, *, min_confidence: float = 0.0) -> List[Detection]:
        self.load()
        with self._inference_lock, torch.inference_mode():
            settings = get_settings()
            precision = resolve_precision(settings.yolo_precision, DEVICE)
            raw_results = self._model(frame, classes=[0], verbose=False,
                conf=min_confidence if min_confidence > 0 else 0.25, device=str(DEVICE),
                imgsz=settings.yolo_image_size, half=precision == "fp16")
            detections: List[Detection] = []
            for result in raw_results:
                names = getattr(result, "names", {}) or {}
                for box in result.boxes:
                    detection = self._from_yolo_box(box, names)
                    if detection.confidence < min_confidence:
                        continue
                    detections.append(detection)
        return detections

    def is_loaded(self) -> bool:
        return self._model is not None

    def status(self) -> Dict[str, Any]:
        return {
            **super().status(),
            "model_path": get_settings().yolo_model_path,
            "precision": ("exported" if not get_settings().yolo_model_path.endswith(".pt")
                          else resolve_precision(get_settings().yolo_precision, DEVICE)),
            "device": str(DEVICE),
        }

    @staticmethod
    def _from_yolo_box(box, names: Dict[int, str]) -> Detection:
        xyxy = box.xyxy.detach().cpu().reshape(-1).tolist()
        confidence = float(box.conf.detach().cpu().reshape(-1)[0].item()) if getattr(box, "conf", None) is not None else 0.0
        class_id = int(box.cls.detach().cpu().reshape(-1)[0].item()) if getattr(box, "cls", None) is not None else 0
        return Detection(
            x1=int(xyxy[0]),
            y1=int(xyxy[1]),
            x2=int(xyxy[2]),
            y2=int(xyxy[3]),
            confidence=confidence,
            class_id=class_id,
            label=str(names.get(class_id, "person")),
        )


class PlannedEmbeddingService(EmbeddingService):
    """Reserved adapter for future self-hosted, ONNX, TensorRT, or remote ReID models."""

    def __init__(self, backend: str, model_path: str = "", endpoint_url: str = "") -> None:
        self.backend = backend
        self.name = f"{backend} embedding adapter"
        self.model_path = model_path
        self.endpoint_url = endpoint_url

    def load(self) -> None:
        return None

    def embed_image(self, image: Image.Image) -> torch.Tensor:
        raise RuntimeError(f"Embedding backend '{self.backend}' is reserved but not implemented yet")

    def embed_text(self, text: str) -> torch.Tensor:
        raise RuntimeError(f"Embedding backend '{self.backend}' is reserved but not implemented yet")

    def is_loaded(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {
            **super().status(),
            "ready": False,
            "model_path": self.model_path,
            "endpoint_url": self.endpoint_url,
        }


class PlannedDetectionService(DetectionService):
    """Reserved adapter for future detector implementations."""

    def __init__(self, backend: str, model_path: str = "", endpoint_url: str = "") -> None:
        self.backend = backend
        self.name = f"{backend} detection adapter"
        self.model_path = model_path
        self.endpoint_url = endpoint_url

    def load(self) -> None:
        return None

    def detect_people(self, frame, *, min_confidence: float = 0.0) -> List[Detection]:
        raise RuntimeError(f"Detection backend '{self.backend}' is reserved but not implemented yet")

    def is_loaded(self) -> bool:
        return False

    def status(self) -> Dict[str, Any]:
        return {
            **super().status(),
            "ready": False,
            "model_path": self.model_path,
            "endpoint_url": self.endpoint_url,
        }


class BaselineReIDModelService(ReIDModelService):
    def __init__(self, embedding_service: EmbeddingService, detection_service: DetectionService) -> None:
        self.embedding_service = embedding_service
        self.detection_service = detection_service

    def load(self) -> None:
        self.embedding_service.load()
        self.detection_service.load()

    def embed_image(self, image: Image.Image) -> torch.Tensor:
        return self.embedding_service.embed_image(image)

    def embed_text(self, text: str) -> torch.Tensor:
        return self.embedding_service.embed_text(text)

    def detect_people(self, frame, *, min_confidence: float = 0.0) -> List[Detection]:
        return self.detection_service.detect_people(frame, min_confidence=min_confidence)

    def status(self) -> Dict[str, Any]:
        embedding_status = self.embedding_service.status()
        detection_status = self.detection_service.status()
        return {
            "backend": get_settings().model_service_backend,
            "ready": bool(embedding_status.get("loaded") and detection_status.get("loaded")),
            "device": str(DEVICE),
            "embedding": embedding_status,
            "detection": detection_status,
        }


def create_embedding_service() -> EmbeddingService:
    settings = get_settings()
    backend = settings.embedding_backend.lower()
    if backend in {"clipreid", "clip_reid", "msmt17"}:
        return CLIPReIDEmbeddingService()
    if backend == "clip":
        return CLIPEmbeddingService()
    if backend in {"custom", "selfhosted", "self_hosted"}:
        return PlannedEmbeddingService(backend, model_path=settings.custom_reid_model_path)
    if backend == "onnx":
        return PlannedEmbeddingService(backend, model_path=settings.onnx_model_path)
    if backend in {"tensorrt", "trt"}:
        return PlannedEmbeddingService(backend, model_path=settings.tensorrt_engine_path)
    if backend in {"remote", "gpu_service", "gpu"}:
        return PlannedEmbeddingService(backend, endpoint_url=settings.gpu_inference_url)
    raise RuntimeError(f"Unsupported EMBEDDING_BACKEND: {settings.embedding_backend}")


def create_detection_service() -> DetectionService:
    settings = get_settings()
    backend = settings.detection_backend.lower()
    if backend == "yolo":
        return YOLODetectionService()
    if backend in {"custom", "selfhosted", "self_hosted"}:
        return PlannedDetectionService(backend, model_path=settings.custom_detection_model_path)
    if backend == "onnx":
        return PlannedDetectionService(backend, model_path=settings.onnx_detection_model_path or settings.onnx_model_path)
    if backend in {"tensorrt", "trt"}:
        return PlannedDetectionService(backend, model_path=settings.tensorrt_detection_engine_path or settings.tensorrt_engine_path)
    if backend in {"remote", "gpu_service", "gpu"}:
        return PlannedDetectionService(backend, endpoint_url=settings.gpu_inference_url)
    raise RuntimeError(f"Unsupported DETECTION_BACKEND: {settings.detection_backend}")


def create_reid_model_service() -> ReIDModelService:
    backend = get_settings().model_service_backend.lower()
    if backend in {"baseline", "local", "clip_yolo"}:
        return BaselineReIDModelService(create_embedding_service(), create_detection_service())
    if backend in {"custom", "onnx", "tensorrt", "trt", "remote", "gpu_service", "gpu"}:
        return BaselineReIDModelService(create_embedding_service(), create_detection_service())
    raise RuntimeError(f"Unsupported MODEL_SERVICE_BACKEND: {get_settings().model_service_backend}")


model_service = create_reid_model_service()


def load_models() -> ReIDModelService:
    # Embeddings are loaded on first use under a project lease.
    model_service.detection_service.load()
    return model_service


def clip_image_feature(image: Image.Image) -> torch.Tensor:
    from app.services.project_models import current_binding
    binding = current_binding()
    if binding:
        return binding.embedding().embed_image(image)
    return model_service.embed_image(image)


def clip_text_feature(text: str) -> torch.Tensor:
    from app.services.project_models import current_binding
    from fastapi import HTTPException
    binding = current_binding()
    if binding:
        if not binding.snapshot["capabilities"]["text"]:
            raise HTTPException(422, detail="该项目模型不支持文本检索，请使用图片检索")
        return binding.embedding().embed_text(text)
    return model_service.embed_text(text)


def detect_people(frame, *, min_confidence: float = 0.0) -> List[Detection]:
    return model_service.detect_people(frame, min_confidence=min_confidence)


def model_status() -> Dict[str, Any]:
    from app.services.project_models import cache_status
    from app.services.model_registry import public_models
    models = public_models()
    status = model_service.status()
    return {**status, "ready": bool(status["detection"]["loaded"] and any(m["available"] for m in models)),
            "models": models, "model_cache": cache_status()}


def is_clip_loaded() -> bool:
    embedding = getattr(model_service, "embedding_service", None)
    return bool(embedding and embedding.backend in {"clip", "clipreid"} and embedding.is_loaded())


def is_yolo_loaded() -> bool:
    detection = getattr(model_service, "detection_service", None)
    return bool(detection and detection.backend == "yolo" and detection.is_loaded())


def clip_image_features(images: List[Image.Image]) -> torch.Tensor:
    from app.services.project_models import current_binding
    binding = current_binding()
    embedding = binding.embedding() if binding else model_service.embedding_service
    if hasattr(embedding, "embed_images"):
        return embedding.embed_images(images)
    return torch.cat([embedding.embed_image(image) for image in images], dim=0)


class MGDDPMEmbeddingService(EmbeddingService):
    name = "MGD-DPM"
    backend = "mgddpm"

    def __init__(self, path, *, snapshot=None):
        self.path = path
        self.image_size = (snapshot or {}).get("preprocess", {}).get("size", [256, 128])
        self.options = (snapshot or {}).get("options", {"stride_size": 12, "camera_index": 0, "sie_coefficient": 1.0})
        self._model = None
        self._lock = Lock()
        self._inference_lock = Lock()

    def load(self):
        with self._lock:
            if self._model is not None:
                return
            CLIPReIDEmbeddingService._ensure_project_root_importable()
            from model.mgddpm import load_teacher
            from torchvision.transforms import Compose, Normalize, Resize, ToTensor, InterpolationMode
            self._preprocess = Compose([Resize(tuple(self.image_size), interpolation=InterpolationMode.BICUBIC),
                                        ToTensor(), Normalize((.485, .456, .406), (.229, .224, .225))])
            self._model = load_teacher(self.path, image_size=self.image_size, stride=self.options["stride_size"],
                                       camera_index=self.options["camera_index"], sie_coefficient=self.options["sie_coefficient"]).to(DEVICE).float().eval()

    def embed_images(self, images):
        self.load()
        with self._inference_lock, torch.inference_mode():
            inputs = torch.stack([self._preprocess(image.convert("RGB")) for image in images]).to(DEVICE)
            features = (self._model(inputs) + self._model(inputs.flip([3]))) / 2.0
            return F.normalize(features, p=2, dim=1)

    def embed_image(self, image):
        return self.embed_images([image])

    def embed_text(self, text):
        raise ValueError("MGD-DPM does not support text search")

    def is_loaded(self):
        return self._model is not None
