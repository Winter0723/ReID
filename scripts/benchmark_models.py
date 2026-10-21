"""Measure actual local model latency; synthetic inputs test execution, not accuracy."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--precision", choices=["fp32", "fp16", "int8"], default="fp16")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--image", type=Path, help="Optional local representative person crop")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    os.environ.update(APP_ENV="test", MODEL_DEVICE=args.device, CLIPREID_PRECISION=args.precision,
                      YOLO_PRECISION="fp16" if args.precision == "fp16" else "fp32")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "FastAPIBackend"))
    import numpy as np
    import torch
    from PIL import Image
    from app.services.model_runtime import CLIPReIDEmbeddingService, YOLODetectionService
    if args.device == "cpu":
        torch.set_num_threads(min(4, os.cpu_count() or 1))
    if args.image:
        image = Image.open(args.image).convert("RGB")
    else:
        image = Image.fromarray(np.random.default_rng(7).integers(0, 256, (336, 160, 3), dtype=np.uint8))
    detector_frame = np.asarray(image.resize((640, 480)))[:, :, ::-1].copy()
    embedding, detector = CLIPReIDEmbeddingService(), YOLODetectionService()
    def sync():
        if args.device == "cuda":
            torch.cuda.synchronize()
    def measure(call):
        for _ in range(3):
            call()
        sync()
        values = []
        for _ in range(args.iterations):
            began = time.perf_counter()
            call()
            sync()
            values.append((time.perf_counter() - began) * 1000)
        return {"median_ms": round(float(np.median(values)), 2), "p95_ms": round(float(np.percentile(values, 95)), 2)}
    result = {"device": args.device, "precision": args.precision, "synthetic_input": not bool(args.image),
              "iterations": args.iterations,
              "vit_image": measure(lambda: embedding.embed_image(image)),
              "vit_text": measure(lambda: embedding.embed_text("a person wearing a blue shirt")),
              "yolo": measure(lambda: detector.detect_people(detector_frame))}
    feature = embedding.embed_image(image)
    result["embedding_finite"] = bool(torch.isfinite(feature).all())
    result["embedding_shape"] = list(feature.shape)
    result["embedding_norm"] = round(float(feature.norm()), 5)
    # Compare the same checkpoint's FP32 representation; this is numerical
    # drift, not a substitute for held-out ReID rank-1/mAP validation.
    from app.core.config import get_settings
    get_settings().clipreid_precision = "fp32"
    baseline = CLIPReIDEmbeddingService().embed_image(image)
    result["cosine_to_fp32"] = round(float((feature * baseline).sum()), 6)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
