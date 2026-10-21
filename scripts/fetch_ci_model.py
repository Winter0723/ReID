"""Fetch the pinned upstream YOLO smoke-test checkpoint, verifying before use."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"


def main():
    item = next(x for x in json.loads((ROOT / "model/checkpoints.json").read_text(encoding="utf-8"))
                if x["path"] == "FastAPIBackend/yolov8n.pt")
    target = ROOT / item["path"]
    if target.exists():
        data = target.read_bytes()
    else:
        with urllib.request.urlopen(URL, timeout=120) as response:
            data = response.read(item["bytes"] + 1)
    if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise SystemExit("YOLO checksum mismatch; refusing to install unverified weights")
    if not target.exists():
        with target.open("xb") as output:
            output.write(data)
    print("Verified upstream YOLO checkpoint")


if __name__ == "__main__":
    main()
