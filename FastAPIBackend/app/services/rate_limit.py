import hashlib
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

from app.core.config import PRODUCTION_LIKE_ENVIRONMENTS, get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitRule:
    dimension: str
    value: str
    limit: int
    window_seconds: int


class RedisRateLimiter:
    """Small Redis-backed fixed-window limiter with privacy-safe keys."""

    _INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {current, redis.call('TTL', KEYS[1])}
"""

    def __init__(self) -> None:
        settings = get_settings()
        self.redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        self._retry_after = 0.0
        self._warning_logged = False

    def enforce(self, action: str, *rules: RateLimitRule) -> None:
        environment = get_settings().app_env.strip().lower()
        if time.monotonic() < getattr(self, "_retry_after", 0.0):
            if environment in PRODUCTION_LIKE_ENVIRONMENTS:
                raise HTTPException(status_code=503, detail="认证限流服务暂不可用")
            return
        try:
            for rule in rules:
                if not rule.value:
                    continue
                key = self._key(action, rule.dimension, rule.value)
                current, ttl = self.redis.eval(
                    self._INCREMENT_SCRIPT,
                    1,
                    key,
                    max(1, rule.window_seconds),
                )
                if int(current) > rule.limit:
                    retry_after = max(1, int(ttl))
                    raise HTTPException(
                        status_code=429,
                        detail="请求过于频繁，请稍后再试",
                        headers={"Retry-After": str(retry_after)},
                    )
            self._retry_after = 0.0
            self._warning_logged = False
        except HTTPException:
            raise
        except RedisError as exc:
            self._retry_after = time.monotonic() + 30.0
            if environment in PRODUCTION_LIKE_ENVIRONMENTS:
                raise HTTPException(status_code=503, detail="认证限流服务暂不可用") from exc
            if not getattr(self, "_warning_logged", False):
                logger.warning("Rate limiter unavailable; allowing request in %s: %s", environment, exc)
                self._warning_logged = True

    @staticmethod
    def _key(action: str, dimension: str, value: str) -> str:
        normalized = value.strip().lower().encode("utf-8")
        digest = hashlib.sha256(normalized).hexdigest()
        return f"myreid:rate:{action}:{dimension}:{digest}"


def request_ip(request: Request) -> str:
    """Use the ASGI peer address; trusted proxy normalization belongs at the edge."""

    return request.client.host if request.client else "unknown"


rate_limiter = RedisRateLimiter()
