import pytest
from fastapi import HTTPException


class _CountingRedis:
    def __init__(self):
        self.counts = {}

    def eval(self, _script, _key_count, key, window_seconds):
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], int(window_seconds)]


def test_redis_rate_limiter_enforces_hashed_dimensions():
    from app.services.rate_limit import RateLimitRule, RedisRateLimiter

    limiter = RedisRateLimiter.__new__(RedisRateLimiter)
    limiter.redis = _CountingRedis()
    rule = RateLimitRule("email", "Sensitive@Example.com", 2, 60)

    limiter.enforce("email-code", rule)
    limiter.enforce("email-code", rule)
    with pytest.raises(HTTPException) as exc_info:
        limiter.enforce("email-code", rule)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["Retry-After"] == "60"
    key = next(iter(limiter.redis.counts))
    assert "sensitive@example.com" not in key


def test_config_rejects_placeholder_inference_backends():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(model_service_backend="onnx")
    with pytest.raises(ValidationError):
        Settings(embedding_backend="remote")
    with pytest.raises(ValidationError):
        Settings(detection_backend="tensorrt")


def test_config_accepts_minio_storage_mode_used_by_compose_templates():
    from app.core.config import Settings

    assert Settings(object_storage_mode="minio").object_storage_mode == "minio"
