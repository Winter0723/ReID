from types import SimpleNamespace


def test_unmatched_routes_use_a_bounded_metrics_label():
    from app.core.observability import UNMATCHED_ROUTE_LABEL, route_template

    request = SimpleNamespace(scope={}, url=SimpleNamespace(path="/missing/attacker-controlled-123"))

    assert route_template(request) == UNMATCHED_ROUTE_LABEL


def test_request_id_rejects_unbounded_or_unsafe_values():
    from app.core.observability import begin_request, clear_request

    try:
        accepted = begin_request("client-request_123")
        oversized = begin_request("x" * 129)
        unsafe = begin_request("contains spaces")

        assert accepted == "client-request_123"
        assert oversized != "x" * 129
        assert unsafe != "contains spaces"
        assert len(oversized) == 36
        assert len(unsafe) == 36
    finally:
        clear_request()
