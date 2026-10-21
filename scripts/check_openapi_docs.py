from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
DOCS_PATH = ROOT_DIR / "docs" / "API.md"
RUNTIME_DIR = Path(tempfile.gettempdir()) / "myreid_openapi_contract"

sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{RUNTIME_DIR / 'core.db'}")
os.environ.setdefault("SCENIC_DATABASE_URL", f"sqlite:///{RUNTIME_DIR / 'scenic.db'}")
os.environ.setdefault("SECRET_KEY", "openapi-contract-only-secret")
os.environ.setdefault("OBJECT_STORAGE_MODE", "local")
os.environ.setdefault("OBJECT_STORAGE_DIR", str(RUNTIME_DIR / "objects"))
os.environ.setdefault("VECTOR_BACKEND", "memory")
os.environ.setdefault("EMAIL_CODE_DEV_MODE", "1")


def main() -> None:
    import main as backend
    from app.services.api_docs_contract import (
        assert_api_docs_match_openapi,
        documented_operations,
    )

    assert_api_docs_match_openapi(backend.app.openapi(), DOCS_PATH)
    count = len(documented_operations(DOCS_PATH))
    print(f"API documentation matches OpenAPI: {count} method/path operations")


if __name__ == "__main__":
    main()
