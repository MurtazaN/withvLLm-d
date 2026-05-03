"""Cache abstraction with in-memory and Redis backends."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeVar

import redis
from cachetools import TTLCache


T = TypeVar("T")


class Cache(Protocol[T]):
    """Cache interface used by enrichment tools."""

    def get_or_compute(self, key: str, compute: Callable[[], T], ttl_seconds: float) -> T:
        """Return cached value if fresh, otherwise compute, store, and return."""


@dataclass(frozen=True)
class _Entry:
    expires_at: float
    value: Any


class InMemoryCache(Cache[T]):
    """Cache backed by an in-memory TTL cache.

    TTLCache provides bounded storage and automatic eviction. Per-entry TTL
    is enforced via the stored expiry timestamp in each entry.
    """

    def __init__(
        self,
        maxsize: int = 2048,
        max_ttl_seconds: float = 7 * 24 * 60 * 60,
        timer: Callable[[], float] | None = None,
    ) -> None:
        self._timer = timer or time.monotonic
        self._cache: TTLCache[str, _Entry] = TTLCache(
            maxsize=maxsize,
            ttl=max_ttl_seconds,
            timer=self._timer,
        )

    def get_or_compute(self, key: str, compute: Callable[[], T], ttl_seconds: float) -> T:
        if ttl_seconds <= 0:
            return compute()

        now = self._timer()
        entry = self._cache.get(key)
        if entry and entry.expires_at > now:
            return entry.value
        if entry:
            self._cache.pop(key, None)

        value = compute()
        self._cache[key] = _Entry(expires_at=now + ttl_seconds, value=value)
        return value


class RedisCache(Cache[T]):
    """Cache backed by Redis using JSON serialization."""

    def __init__(self, redis_url: str, timeout_seconds: float = 0.5) -> None:
        self._redis_url = redis_url
        self._timeout_seconds = timeout_seconds
        self._logger = logging.getLogger("soc-claw.cache")
        self._client = self._build_client()

    def _build_client(self) -> redis.Redis:
        return redis.Redis.from_url(
            self._redis_url,
            socket_timeout=self._timeout_seconds,
            socket_connect_timeout=self._timeout_seconds,
            decode_responses=True,
        )

    def _call_redis(self, func: Callable[..., Any], *args: Any) -> tuple[bool, Any]:
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                return True, func(*args)
            except redis.RedisError as exc:
                last_exc = exc
                self._client = self._build_client()
        self._logger.warning(
            "Redis cache unavailable; bypassing cache",
            extra={"error": str(last_exc) if last_exc else "unknown"},
        )
        return False, None

    def get_or_compute(self, key: str, compute: Callable[[], T], ttl_seconds: float) -> T:
        if ttl_seconds <= 0:
            return compute()

        ok, raw = self._call_redis(self._client.get, key)
        if not ok:
            return compute()

        if raw is not None:
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                self._logger.warning(
                    "Redis cache entry invalid JSON; deleting",
                    extra={"key": key, "error": str(exc)},
                )
                self._call_redis(self._client.delete, key)

        value = compute()
        try:
            payload = json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise TypeError("RedisCache requires JSON-serializable values") from exc

        ttl = int(math.ceil(ttl_seconds))
        self._call_redis(self._client.setex, key, ttl, payload)
        return value


def get_cache() -> Cache[Any]:
    """Return the configured cache backend based on SOC_CLAW_REDIS_URL."""

    redis_url = os.environ.get("SOC_CLAW_REDIS_URL", "").strip()
    if redis_url:
        return RedisCache(redis_url)
    return InMemoryCache()
