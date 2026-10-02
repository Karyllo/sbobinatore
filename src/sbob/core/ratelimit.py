"""Rate limiter a finestra scorrevole (60s), thread-safe.

A differenza della versione originale non dorme tenendo il lock: calcola l'attesa,
rilascia il lock, dorme e ritenta. Così i thread su chiavi diverse non si bloccano.
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock


class RateLimiter:
    def __init__(self, requests_per_minute: int, window: float = 60.0):
        self.rpm = max(1, int(requests_per_minute))
        self.window = window
        self._ts: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._ts and self._ts[0] <= now - self.window:
                    self._ts.popleft()
                if len(self._ts) < self.rpm:
                    self._ts.append(now)
                    return
                wait = self.window - (now - self._ts[0]) + 0.05
            time.sleep(max(wait, 0.05))


class LimiterPool:
    """Un limiter per (provider, chiave, modello)."""

    def __init__(self):
        self._limiters: dict[tuple[str, str, str], RateLimiter] = {}
        self._lock = Lock()

    def get(self, provider: str, key: str, model: str, rpm: int) -> RateLimiter:
        k = (provider, key, model)
        with self._lock:
            if k not in self._limiters:
                self._limiters[k] = RateLimiter(rpm)
            return self._limiters[k]
