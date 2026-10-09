"""Rate limit: un token quemado se corta, uno normal no se entera."""

from __future__ import annotations

import pytest

from app.ratelimit import Bucket, RateLimiter


def test_allows_up_to_the_limit() -> None:
    limiter = RateLimiter(writes_per_min=3, reads_per_min=10)
    for i in range(3):
        allowed, retry = limiter.check("write", "tok1", now=0.0)
        assert allowed, f"la request {i} debería pasar"
        assert retry == 0


def test_blocks_the_request_that_exceeds() -> None:
    limiter = RateLimiter(writes_per_min=3, reads_per_min=10)
    for _ in range(3):
        limiter.check("write", "tok1", now=0.0)
    allowed, retry = limiter.check("write", "tok1", now=1.0)
    assert not allowed
    assert retry > 0


def test_tokens_are_independent() -> None:
    """Un agente que se pasa mucho no frena al resto del equipo."""
    limiter = RateLimiter(writes_per_min=2, reads_per_min=10)
    assert limiter.check("write", "tok1", now=0.0)[0]
    assert limiter.check("write", "tok1", now=0.0)[0]
    assert not limiter.check("write", "tok1", now=0.0)[0]
    assert limiter.check("write", "tok2", now=0.0)[0]


def test_window_slides() -> None:
    limiter = RateLimiter(writes_per_min=2, reads_per_min=10)
    limiter.check("write", "tok1", now=0.0)
    limiter.check("write", "tok1", now=1.0)
    assert not limiter.check("write", "tok1", now=2.0)[0]
    # Pasados los 60s vuelve a entrar.
    assert limiter.check("write", "tok1", now=61.0)[0]


def test_reads_and_writes_have_separate_budgets() -> None:
    limiter = RateLimiter(writes_per_min=2, reads_per_min=100)
    limiter.check("write", "tok1", now=0.0)
    limiter.check("write", "tok1", now=0.0)
    assert not limiter.check("write", "tok1", now=0.0)[0]
    assert limiter.check("read", "tok1", now=0.0)[0]


def test_retry_after_is_sane() -> None:
    limiter = RateLimiter(writes_per_min=1, reads_per_min=1)
    limiter.check("write", "tok1", now=0.0)
    allowed, retry = limiter.check("write", "tok1", now=10.0)
    assert not allowed
    assert 1 <= retry <= 60


def test_idle_buckets_are_evicted() -> None:
    """Sin esto, el dict crece con cada token que se vio alguna vez."""
    limiter = RateLimiter(writes_per_min=5, reads_per_min=5)
    for i in range(50):
        limiter.check("write", f"tok{i}", now=0.0)
    assert len(limiter._buckets) == 50  # noqa: SLF001

    # Un token que vuelve después de la ventana se re-crea y el viejo desaparece.
    limiter.check("write", "tok0", now=1000.0)
    assert len(limiter._buckets) == 1  # noqa: SLF001
    assert ("write", "tok0") in limiter._buckets  # noqa: SLF001


def test_bucket_retry_after_when_empty() -> None:
    assert Bucket(limit=1).retry_after(now=0.0) == 0


def test_limits_come_from_settings() -> None:
    from app.ratelimit import get_limiter

    limiter = get_limiter()
    assert limiter.writes_per_min > 0
    assert limiter.reads_per_min >= limiter.writes_per_min


@pytest.mark.parametrize("kind", ["read", "write"])
def test_configurable_limits_are_respected(kind: str) -> None:
    limiter = RateLimiter(writes_per_min=1, reads_per_min=1)
    assert limiter.check(kind, "tok", now=0.0)[0]
    assert not limiter.check(kind, "tok", now=0.0)[0]
