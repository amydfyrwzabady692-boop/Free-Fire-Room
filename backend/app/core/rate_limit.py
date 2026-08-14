from __future__ import annotations

from app.core.errors import RateLimitError
from app.core.redis import get_redis


async def hit_rate_limit(key: str, limit: int, window_seconds: int = 60) -> None:
    redis = get_redis()
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, window_seconds)
    if n > limit:
        ttl = await redis.ttl(key)
        raise RateLimitError(
            "rate_limited",
            "تعداد درخواست بیش از حد مجاز است. کمی بعد دوباره تلاش کنید.",
            details={"retry_after": max(ttl, 1)},
        )


async def acquire_lock(key: str, ttl_seconds: int = 30, token: str = "1") -> bool:
    redis = get_redis()
    return bool(await redis.set(key, token, nx=True, ex=ttl_seconds))


async def release_lock(key: str, token: str = "1") -> None:
    redis = get_redis()
    current = await redis.get(key)
    if current == token:
        await redis.delete(key)
