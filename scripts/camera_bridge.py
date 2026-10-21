"""Compatibility entry point; implementation lives in the standalone camera_gateway package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera_gateway.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())
