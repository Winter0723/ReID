from pathlib import Path
import json
import os
import sys
import tempfile


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
FRONTEND_CLIENT = ROOT_DIR / "QtFrontend" / "SearchClient.cpp"
RUNTIME_DIR = Path(
    os.getenv(
        "MYREID_FRONTEND_SMOKE_DIR",
        str(Path(tempfile.gettempdir()) / "myreid_frontend_smoke"),
    )
)

sys.path.insert(0, str(BACKEND_DIR))
os.chdir(BACKEND_DIR)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{RUNTIME_DIR / 'frontend_smoke.db'}")
os.environ.setdefault("OBJECT_STORAGE_MODE", "local")
os.environ.setdefault("OBJECT_STORAGE_DIR", str(RUNTIME_DIR / "object_storage"))
os.environ.setdefault("VECTOR_BACKEND", "memory")
os.environ.setdefault("EMAIL_CODE_DEV_MODE", "1")


CRITICAL_FRONTEND_ROUTES = {
    ("/api/v1/readyz", "GET"),
    ("/api/v1/auth/register", "POST"),
    ("/api/v1/auth/login", "POST"),
    ("/api/v1/auth/email-code/request", "POST"),
    ("/api/v1/auth/email-code/login", "POST"),
    ("/api/v1/auth/me", "GET"),
    ("/api/v1/projects", "GET"),
    ("/api/v1/projects", "POST"),
    ("/api/v1/projects/{project_id}/media", "POST"),
    ("/api/v1/projects/{project_id}/media/batch", "POST"),
    ("/api/v1/projects/{project_id}/status", "GET"),
    ("/api/v1/projects/{project_id}/preview", "GET"),
    ("/api/v1/projects/{project_id}/jobs", "GET"),
    ("/api/v1/search/text", "POST"),
    ("/api/v1/search/image", "POST"),
    ("/api/v1/history", "GET"),
    ("/api/v1/jobs/{job_id}", "GET"),
}

FORBIDDEN_LEGACY_LITERALS = {
    '"/register"',
    '"/login"',
    '"/upload/gallery"',
    '"/upload/video"',
    '"/search/text"',
    '"/search/image"',
    '"/gallery/status"',
}


def main():
    import main as backend

    routes = {
        (route.path, method)
        for route in backend.app.routes
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    }
    missing = sorted(CRITICAL_FRONTEND_ROUTES - routes)
    if missing:
        raise AssertionError(f"Backend is missing frontend-critical routes: {missing}")

    source = FRONTEND_CLIENT.read_text(encoding="utf-8")
    legacy = sorted(item for item in FORBIDDEN_LEGACY_LITERALS if item in source)
    if legacy:
        raise AssertionError(f"Qt frontend still references legacy API literals: {legacy}")
    if '"/api/v1/search/text"' not in source or '"/api/v1/projects"' not in source:
        raise AssertionError("Qt frontend does not reference the expected v1 search/project endpoints")

    print(json.dumps({
        "status": "frontend api smoke ok",
        "checked_routes": len(CRITICAL_FRONTEND_ROUTES),
        "source": str(FRONTEND_CLIENT.relative_to(ROOT_DIR)),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
