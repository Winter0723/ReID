import ast
from pathlib import Path

import database
import scenic_database

from app.db.orm_mixins import CreatedAtMixin, IntegerIdMixin, TimestampMixin


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_supported_routers_do_not_depend_on_catch_all_common_module():
    router_paths = list((BACKEND_DIR / "app" / "api" / "routes").glob("*.py"))
    router_paths += list((BACKEND_DIR / "app" / "api" / "scenic").glob("*_routes.py"))
    offenders = [
        str(path.relative_to(BACKEND_DIR))
        for path in router_paths
        if "app.api.common" in _imports(path)
    ]
    assert offenders == []


def test_focused_services_do_not_delegate_back_to_common():
    service_names = (
        "auth_service.py",
        "email_service.py",
        "permission_service.py",
        "audit_service.py",
        "media_service.py",
        "search_service.py",
        "task_service.py",
        "persistence_service.py",
        "observability_service.py",
    )
    offenders = [
        service_name
        for service_name in service_names
        if "app.api.common" in _imports(BACKEND_DIR / "app" / "services" / service_name)
    ]
    assert offenders == []


def test_scenic_api_is_composed_from_six_focused_routers():
    source = (BACKEND_DIR / "app" / "api" / "scenic" / "__init__.py").read_text(encoding="utf-8")
    for module_name in (
        "auth_routes",
        "project_routes",
        "camera_routes",
        "tourist_routes",
        "vlog_routes",
        "order_routes",
    ):
        assert f"router.include_router({module_name}.router)" in source
    assert "APIRouter" not in (BACKEND_DIR / "app" / "api" / "scenic" / "system_routes.py").read_text(
        encoding="utf-8"
    )


def test_runtime_patch_import_side_effects_are_gone():
    python_sources = (BACKEND_DIR / "app").rglob("*.py")
    offenders = []
    for path in python_sources:
        imports = _imports(path)
        if {
            "app.services.camera_stream_runtime_patch",
            "app.services.video_runtime_patch",
        } & imports:
            offenders.append(str(path.relative_to(BACKEND_DIR)))
    assert offenders == []


def test_both_domains_use_shared_orm_mixins():
    for db_module in (database, scenic_database):
        assert issubclass(db_module.User, IntegerIdMixin)
        assert issubclass(db_module.User, CreatedAtMixin)
        assert issubclass(db_module.Project, IntegerIdMixin)
        assert issubclass(db_module.Project, TimestampMixin)
        assert issubclass(db_module.ProcessingJob, TimestampMixin)
