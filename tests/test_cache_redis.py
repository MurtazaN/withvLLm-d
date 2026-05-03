import os
import time

import pytest
import redis

from soc_claw.cache import RedisCache


REDIS_URL = os.environ.get("SOC_CLAW_REDIS_URL", "redis://127.0.0.1:6379/0")


@pytest.fixture()
def redis_cache():
    client = redis.Redis.from_url(
        REDIS_URL,
        socket_timeout=0.2,
        socket_connect_timeout=0.2,
        decode_responses=True,
    )
    try:
        client.ping()
        client.flushdb()
    except redis.RedisError:
        pytest.skip("Redis unavailable")
    return RedisCache(REDIS_URL)


def test_redis_cache_miss_then_hit(redis_cache):
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"value": calls["count"]}

    first = redis_cache.get_or_compute("ip_rep:1.2.3.4", compute, ttl_seconds=10)
    second = redis_cache.get_or_compute("ip_rep:1.2.3.4", compute, ttl_seconds=10)

    assert first == second
    assert calls["count"] == 1


def test_redis_cache_expiry_triggers_recompute(redis_cache):
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"value": calls["count"]}

    redis_cache.get_or_compute("asset:host-1", compute, ttl_seconds=1)
    time.sleep(1.2)
    redis_cache.get_or_compute("asset:host-1", compute, ttl_seconds=1)

    assert calls["count"] == 2


def test_redis_cache_negative_result_is_cached(redis_cache):
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"verdict": "unknown"}

    first = redis_cache.get_or_compute("ip_rep:8.8.8.8", compute, ttl_seconds=10)
    second = redis_cache.get_or_compute("ip_rep:8.8.8.8", compute, ttl_seconds=10)

    assert first == {"verdict": "unknown"}
    assert second == first
    assert calls["count"] == 1


def test_redis_cache_unavailable_falls_back():
    url = "redis://127.0.0.1:6399/0"
    probe = redis.Redis.from_url(
        url,
        socket_timeout=0.05,
        socket_connect_timeout=0.05,
        decode_responses=True,
    )
    try:
        probe.ping()
    except redis.RedisError:
        pass
    else:
        pytest.skip("Redis available on fallback test port")

    cache = RedisCache(url, timeout_seconds=0.01)
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"value": calls["count"]}

    first = cache.get_or_compute("ip_rep:9.9.9.9", compute, ttl_seconds=10)
    second = cache.get_or_compute("ip_rep:9.9.9.9", compute, ttl_seconds=10)

    assert first == {"value": 1}
    assert second == {"value": 2}
    assert calls["count"] == 2
