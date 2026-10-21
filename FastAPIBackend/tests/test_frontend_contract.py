from pathlib import Path


def test_production_client_critical_routes_exist_in_backend():
    import main

    routes = {
        (route.path, method)
        for route in main.app.routes
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    }

    expected = {
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
        ("/api/v1/search/text", "POST"),
        ("/api/v1/search/image", "POST"),
        ("/api/v1/history", "GET"),
        ("/api/v1/jobs/{job_id}", "GET"),
        ("/api/v1/scenic/auth/register", "POST"),
        ("/api/v1/scenic/auth/login", "POST"),
        ("/api/v1/scenic/auth/me", "GET"),
        ("/api/v1/scenic/projects", "GET"),
        ("/api/v1/scenic/projects", "POST"),
        ("/api/v1/scenic/projects/{project_id}/dashboard", "GET"),
        ("/api/v1/scenic/projects/{project_id}/camera-points", "GET"),
        ("/api/v1/scenic/projects/{project_id}/camera-points", "POST"),
        ("/api/v1/scenic/projects/{project_id}/camera-points/discover", "POST"),
        ("/api/v1/scenic/projects/{project_id}/camera/start", "POST"),
        ("/api/v1/scenic/projects/{project_id}/camera/stop", "POST"),
        ("/api/v1/scenic/projects/{project_id}/camera/status", "GET"),
        ("/api/v1/scenic/projects/{project_id}/tourists", "GET"),
        ("/api/v1/scenic/projects/{project_id}/tourists", "POST"),
        ("/api/v1/scenic/tourists/{session_id}/match", "POST"),
        ("/api/v1/scenic/tourists/{session_id}/segments/{segment_id}", "PATCH"),
        ("/api/v1/scenic/tourists/{session_id}/vlog", "POST"),
        ("/api/v1/scenic/tourists/{session_id}/orders", "POST"),
        ("/api/v1/scenic/tourists/{session_id}/orders/simulate-payment", "POST"),
        ("/api/v1/scenic/orders/{order_id}/refresh-payment", "POST"),
        ("/api/v1/scenic/orders/{order_id}/simulate-payment-result", "POST"),
        ("/api/v1/scenic/orders/{order_id}/mark-paid", "POST"),
        ("/api/v1/scenic/payments/wechat/notify", "POST"),
    }
    assert expected <= routes


def test_pre_v1_demo_routes_are_not_mounted(client):
    response = client.get(
        "/gallery/preview",
        headers={"X-Guest-ID": "guest", "X-Session-ID": "session"},
    )
    assert response.status_code == 403  # Private gallery rejects unsigned legacy requests.


def test_legacy_surfaces_are_contained_in_archive():
    root = Path(__file__).resolve().parents[2]
    assert not (root / "QtFrontend").exists()
    assert not (root / "FastAPIBackend" / "app" / "api" / "legacy.py").exists()
    assert (root / "legacy" / "qt-qml-client" / "CMakeLists.txt").is_file()
    assert (root / "legacy" / "api" / "pre_v1.py").is_file()

    policy = (root / "legacy" / "README.md").read_text(encoding="utf-8")
    assert "Last compatibility version: `0.1.0`" in policy
    assert "production support: stopped" in policy
