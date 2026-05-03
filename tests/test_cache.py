from soc_claw.cache import InMemoryCache, RedisCache, get_cache


class _FakeTimer:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_inmemory_cache_miss_then_hit():
    timer = _FakeTimer()
    cache = InMemoryCache(max_ttl_seconds=1000, timer=timer)
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"value": calls["count"]}

    first = cache.get_or_compute("ip_rep:1.2.3.4", compute, ttl_seconds=10)
    second = cache.get_or_compute("ip_rep:1.2.3.4", compute, ttl_seconds=10)

    assert first == second
    assert calls["count"] == 1


def test_inmemory_cache_expiry_triggers_recompute():
    timer = _FakeTimer()
    cache = InMemoryCache(max_ttl_seconds=1000, timer=timer)
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"value": calls["count"]}

    cache.get_or_compute("asset:host-1", compute, ttl_seconds=10)
    timer.now = 11.0
    cache.get_or_compute("asset:host-1", compute, ttl_seconds=10)

    assert calls["count"] == 2


def test_inmemory_cache_negative_result_is_cached():
    timer = _FakeTimer()
    cache = InMemoryCache(max_ttl_seconds=1000, timer=timer)
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return {"verdict": "unknown"}

    first = cache.get_or_compute("ip_rep:8.8.8.8", compute, ttl_seconds=10)
    second = cache.get_or_compute("ip_rep:8.8.8.8", compute, ttl_seconds=10)

    assert first == {"verdict": "unknown"}
    assert second == first
    assert calls["count"] == 1


def test_get_cache_inmemory(monkeypatch):
    monkeypatch.delenv("SOC_CLAW_REDIS_URL", raising=False)
    cache = get_cache()
    assert isinstance(cache, InMemoryCache)


def test_get_cache_redis(monkeypatch):
    monkeypatch.setenv("SOC_CLAW_REDIS_URL", "redis://localhost:6379/0")
    cache = get_cache()
    assert isinstance(cache, RedisCache)
