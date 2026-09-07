"""Caching for aggregates, keyed by filter and invalidated by the transform.

A breakdown is one metric computed once per slice — thirty sectors is thirty
queries, and the dashboard asks for several breakdowns at once. Each is fast on
this dataset, but the arithmetic of "fast enough, many times" is how a 2s p95
turns into a 6s one.

**Invalidation is a generation counter, not key tracking.** `core` is rebuilt
wholesale by each transform pass, so every cached figure becomes suspect at the
same instant. One integer in Redis is bumped when a pass commits, and it forms
part of every key; the old entries are not deleted, they simply stop being
addressed and expire on their own.

The alternative — remembering which keys a metric touched so they can be
evicted — is a second model of what depends on what, and the failure it
produces is the worst kind: a stale number that looks current, served
indefinitely because nobody remembered to invalidate it.

**A cache miss is never an error.** Redis being down degrades this to computing
every time, which is slower and correct. A dashboard that fails because its
cache failed would be worse than one that is slow.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any, TypeVar, cast

import redis

from lnd.config import get_settings

# redis-py types every command as possibly-awaitable because one class covers
# the sync and async clients. These calls are all synchronous.

log = logging.getLogger(__name__)

T = TypeVar("T")

#: One integer, shared by every cached aggregate.
GENERATION_KEY = "lnd:metrics:generation"
PREFIX = "lnd:metrics"

#: Long, because the generation counter is what actually expires an entry. The
#: TTL is a backstop for the case where a transform bumps the counter and then
#: nothing reads the old keys again — it stops Redis growing without bound.
TTL_SECONDS = 24 * 60 * 60


def _client() -> redis.Redis | None:
    try:
        return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    except (redis.RedisError, ValueError) as exc:  # pragma: no cover - config error
        log.warning(
            "metrics cache unavailable", extra={"event": "cache.unavailable", "error": str(exc)}
        )
        return None


def generation() -> int:
    """The current data generation. 0 when Redis cannot be reached.

    Falling back to 0 rather than raising means an unreachable Redis produces a
    consistent key rather than an exception — and since nothing will be found
    under it either, every request simply computes.
    """
    client = _client()
    if client is None:
        return 0
    try:
        value = cast("str | None", client.get(GENERATION_KEY))
        return int(value) if value is not None else 0
    except (redis.RedisError, ValueError):
        return 0


def invalidate() -> int:
    """Bump the generation. Called when a transform pass commits.

    Every cached aggregate becomes unaddressable at once, which is the correct
    granularity: the transform rebuilds `core` wholesale, so there is no such
    thing as a figure it did not touch.
    """
    client = _client()
    if client is None:
        return 0
    try:
        new = int(cast("int", client.incr(GENERATION_KEY)))
        log.info(
            "metrics cache invalidated", extra={"event": "cache.invalidated", "generation": new}
        )
        return new
    except redis.RedisError:  # pragma: no cover - depends on the server
        return 0


def _canonical(value: Any) -> Any:
    """Filters into something that hashes the same way every time.

    Sets are unordered, so `{"Finance", "Sales"}` and `{"Sales", "Finance"}` are
    the same filter and must produce the same key — otherwise two identical
    requests miss the cache and, worse, the hit rate depends on iteration order.
    """
    if isinstance(value, frozenset | set):
        return sorted(str(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _canonical(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def key_for(kind: str, **parts: Any) -> str:
    """A stable key for one request shape, inside the current generation."""
    payload = json.dumps(_canonical(parts), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{PREFIX}:{generation()}:{kind}:{digest}"


def cached(
    key: str,
    compute: Callable[[], T],
    *,
    serialise: Callable[[T], Any],
    deserialise: Callable[[Any], T],
) -> tuple[T, bool]:
    """Return the cached value, or compute and store it. Second item is the hit.

    Explicit serialise/deserialise rather than pickle: what goes into Redis is
    JSON somebody can read while debugging, and a shape change between
    deployments produces a decode failure that falls through to computing
    rather than an unpickling error at request time.
    """
    client = _client()
    if client is None:
        return compute(), False

    try:
        raw = cast("str | None", client.get(key))
        if raw is not None:
            return deserialise(json.loads(raw)), True
    except (redis.RedisError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # A miss, deliberately. An entry written by an older shape of the code
        # is not a failure to report — it is a value to recompute.
        pass

    value = compute()
    try:
        client.setex(key, TTL_SECONDS, json.dumps(serialise(value)))
    except (redis.RedisError, TypeError, ValueError):  # pragma: no cover
        log.warning("metrics cache write failed", extra={"event": "cache.write_failed", "key": key})
    return value, False
