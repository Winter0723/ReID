from __future__ import annotations

import re
from pathlib import Path
from typing import Any


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
DOCUMENTED_ROUTE_RE = re.compile(
    r"^\|\s*`(GET|POST|PUT|PATCH|DELETE)`\s*\|\s*`([^`]+)`\s*\|",
    re.MULTILINE,
)


def documented_operations(path: Path) -> set[tuple[str, str]]:
    return set(DOCUMENTED_ROUTE_RE.findall(path.read_text(encoding="utf-8")))


def openapi_operations(schema: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), route_path)
        for route_path, path_item in schema.get("paths", {}).items()
        for method in path_item
        if method.upper() in HTTP_METHODS
    }


def operation_drift(
    schema: dict[str, Any],
    docs_path: Path,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    documented = documented_operations(docs_path)
    implemented = openapi_operations(schema)
    return documented - implemented, implemented - documented


def assert_api_docs_match_openapi(schema: dict[str, Any], docs_path: Path) -> None:
    documented = documented_operations(docs_path)
    if not documented:
        raise AssertionError(f"{docs_path} contains no method/path rows")
    missing, undocumented = operation_drift(schema, docs_path)
    if missing or undocumented:
        messages = []
        if missing:
            messages.append(f"documented but absent from OpenAPI: {sorted(missing)}")
        if undocumented:
            messages.append(f"present in OpenAPI but absent from docs: {sorted(undocumented)}")
        raise AssertionError("API documentation drift detected: " + "; ".join(messages))
