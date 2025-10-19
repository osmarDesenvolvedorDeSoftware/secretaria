from __future__ import annotations

import time

from redis import Redis

from app.config import settings
from app.services.tenancy import TenantContext


class RateLimiter:
    def __init__(self, redis_client: Redis, tenant: TenantContext) -> None:
        self.redis = redis_client
        self.tenant = tenant

    def _check_limit(self, key: str, limit: int) -> bool:
        now = time.time()
        window_start = now - settings.rate_limit_window_seconds
        pipeline = self.redis.pipeline()
        pipeline.zremrangebyscore(key, 0, window_start)
        pipeline.zadd(key, {str(now): now})
        pipeline.zcard(key)
        pipeline.expire(key, settings.rate_limit_ttl)
        _, _, count, _ = pipeline.execute()
        return count <= limit

    def check_ip(self, ip: str) -> bool:
        key = self.tenant.namespaced_key("rl", "ip", ip)
        return self._check_limit(key, settings.webhook_rate_limit_ip)

    def check_number(self, number: str) -> bool:
        key = self.tenant.namespaced_key("rl", "num", number)
        return self._check_limit(key, settings.webhook_rate_limit_number)
