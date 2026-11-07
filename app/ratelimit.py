"""Rate limit por token. Token bucket en memoria.

No es distribuido a propósito: SQLite ya obliga a una sola réplica, así que
un dict en memoria es exactamente tan bueno como cualquier otra cosa. Si algún
 día hay más de un worker, esto hay que rehacerlo (y `numReplicas=1` lo impide).

Se aplica por token, no por IP: el caso que queremos cortar es un token
filtrado que alguien está quemando, no un cliente legítimo que va lento.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class Bucket:
    """Ventana deslizante: N requests en los últimos 60 segundos."""

    limit: int
    window_seconds: float = 60.0
    hits: deque[float] = field(default_factory=deque)

    def allow(self, now: float | None = None) -> bool:
        moment = now if now is not None else time.monotonic()
        cutoff = moment - self.window_seconds
        while self.hits and self.hits[0] < cutoff:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(moment)
        return True

    def retry_after(self, now: float | None = None) -> int:
        """Segundos que hay que esperar hasta que se libere un slot."""
        moment = now if now is not None else time.monotonic()
        if not self.hits:
            return 0
        oldest = self.hits[0] + self.window_seconds
        return max(1, int(oldest - moment + 0.999))

    @property
    def used(self) -> int:
        return len(self.hits)


class RateLimiter:
    """Un bucket por clave. Los buckets inactivos se limpian solos."""

    def __init__(self, writes_per_min: int = 120, reads_per_min: int = 600) -> None:
        self.writes_per_min = writes_per_min
        self.reads_per_min = reads_per_min
        self._buckets: dict[tuple[str, str], Bucket] = defaultdict(
            lambda: Bucket(limit=self.writes_per_min)
        )

    def _key(self, kind: str, ident: str) -> tuple[str, str]:
        return (kind, ident)

    def check(self, kind: str, ident: str, now: float | None = None) -> tuple[bool, int]:
        """Devuelve (permitido, retry_after)."""
        limit = self.writes_per_min if kind == "write" else self.reads_per_min
        key = self._key(kind, ident)
        bucket = self._buckets.get(key)
        if bucket is None or bucket.limit != limit:
            bucket = Bucket(limit=limit)
            self._buckets[key] = bucket
        moment = now if now is not None else time.monotonic()
        ok = bucket.allow(moment)
        self._evict(moment)
        return ok, (0 if ok else bucket.retry_after(moment))

    def _evict(self, now: float) -> None:
        """Saca buckets que no receive hits en el doble de la ventana."""
        stale = [
            k for k, b in self._buckets.items() if b.hits and now - b.hits[0] > b.window_seconds * 2
        ]
        for key in stale:
            del self._buckets[key]

    def reset(self) -> None:
        self._buckets.clear()


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        from app.config import settings

        _limiter = RateLimiter(
            settings.rate_limit_writes_per_min, settings.rate_limit_reads_per_min
        )
    return _limiter


def reset_limiter() -> None:
    """Solo para tests."""
    global _limiter
    _limiter = None
