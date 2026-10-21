"""Export a calibrated INT8 detector without changing the deployed model."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True, help="Representative calibration dataset YAML")
    parser.add_argument("--format", choices=["openvino", "engine"], default="openvino")
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    for path in (args.model, args.data):
        if not path.is_file():
            parser.error(f"File does not exist: {path}")
    if args.format == "engine" and args.device == "cpu":
        parser.error("TensorRT export needs --device 0 (or another CUDA device)")
    from ultralytics import YOLO
    # The repository pins ultralytics 8.4.60, whose export option is int8=True.
    result = YOLO(str(args.model.resolve()), task="detect").export(
        format=args.format, int8=True, data=str(args.data.resolve()),
        imgsz=640, batch=1, device=args.device, dynamic=False,
    )
    print(f"Exported: {result}")
    print("Validate recall on held-out camera footage before setting YOLO_MODEL_PATH to this artifact.")


if __name__ == "__main__":
    main()
