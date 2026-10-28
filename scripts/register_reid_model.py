"""Register another local MGD-DPM checkpoint without overwriting an existing model."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "FastAPIBackend"))


def register_model(weights, model_id, name, *, template=None, overrides=None):
    from app.services.model_registry import discover_models, model_directory, validate_descriptor
    source = Path(template) if template else ROOT / "model/mgddpm-market2msmt.model.json"
    descriptor = json.loads(source.read_text(encoding="utf-8"))
    descriptor.update(id=model_id, name=name, weights=weights)
    for key, value in (overrides or {}).items():
        if value is not None:
            if key in ("height", "width"):
                descriptor["preprocess"]["size"][0 if key == "height" else 1] = value
            else:
                descriptor["options"][key] = value
    snapshot = validate_descriptor(descriptor)
    if any(row["id"] == model_id for row in discover_models()):
        raise ValueError("模型 ID 已存在，请使用新的唯一 ID，不覆盖已有项目模型")
    target = model_directory() / f"{model_id}.model.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(descriptor, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return target, snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True, help="Checkpoint path relative to REID_MODEL_DIR")
    parser.add_argument("--id", required=True, help="Unique stable project model ID")
    parser.add_argument("--name", required=True, help="Name displayed in the model selector")
    parser.add_argument("--template", help="Explicit recipe to copy; default: shipped MGD-DPM teacher recipe")
    for key in ("height", "width", "stride-size", "camera-index"):
        parser.add_argument(f"--{key}", type=int)
    parser.add_argument("--sie-coefficient", type=float)
    args = parser.parse_args()
    try:
        path, snapshot = register_model(args.weights, args.id, args.name, template=args.template,
            overrides={key: getattr(args, key) for key in ("height", "width", "stride_size", "camera_index", "sie_coefficient")})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"descriptor": str(path), "id": snapshot["id"], "name": snapshot["name"],
                      "dimension": snapshot["dimension"], "embedding_space_id": snapshot["embedding_space_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
