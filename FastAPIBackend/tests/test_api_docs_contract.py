from pathlib import Path

from fastapi.routing import APIRoute

from app.services.api_docs_contract import assert_api_docs_match_openapi


def test_documented_api_method_paths_match_openapi_schema():
    import main

    docs_path = Path(__file__).resolve().parents[2] / "docs" / "API.md"
    assert_api_docs_match_openapi(main.app.openapi(), docs_path)


def test_scenic_endpoints_are_owned_by_focused_route_modules():
    import main

    scenic_routes = [
        route
        for route in main.app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/scenic")
    ]
    endpoint_modules = {route.endpoint.__module__ for route in scenic_routes}
    assert endpoint_modules == {
        "app.api.scenic.auth_routes",
        "app.api.scenic.camera_routes",
        "app.api.scenic.editing_routes",
        "app.api.scenic.order_routes",
        "app.api.scenic.project_routes",
        "app.api.scenic.tourist_routes",
        "app.api.scenic.vlog_routes",
        "app.api.scenic.visitor_routes",
    }
