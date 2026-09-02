
import pytest

from app.core.rate_limit import hit_rate_limit
from app.core.errors import RateLimitError


@pytest.mark.asyncio
async def test_rate_limit_hits(monkeypatch):
    class DummyRedis:
        def __init__(self):
            self.n = 0

        async def incr(self, key):
            self.n += 1
            return self.n

        async def expire(self, key, window):
            return True

        async def ttl(self, key):
            return 30

    from app.core import rate_limit

    dummy = DummyRedis()
    monkeypatch.setattr(rate_limit, "get_redis", lambda: dummy)
    await hit_rate_limit("k", 2)
    await hit_rate_limit("k", 2)
    with pytest.raises(RateLimitError):
        await hit_rate_limit("k", 2)
