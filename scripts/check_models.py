"""Verify local checkpoints against the published compatibility manifest."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    for item in json.loads((ROOT / "model/checkpoints.json").read_text(encoding="utf-8")):
        path = ROOT / item["path"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise SystemExit(f"Missing or wrong-size model: {item['path']}; see model/README.md")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise SystemExit(f"Model checksum mismatch: {item['path']}")
        print(f"Verified {item['path']}")


if __name__ == "__main__":
    main()
