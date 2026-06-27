from __future__ import annotations
import aiohttp


class CircuitBreaker:
    def __init__(self, threshold: int = 5):
        self._threshold = threshold
        self._fails = 0
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def record_success(self) -> None:
        self._fails = 0
        self._open = False

    def record_failure(self) -> bool:
        self._fails += 1
        if self._fails >= self._threshold and not self._open:
            self._open = True
            return True
        return False


async def ping_dead_man(session: aiohttp.ClientSession, url: str | None) -> bool:
    if not url:
        return False
    try:
        async with session.get(url) as r:
            return 200 <= r.status < 300
    except aiohttp.ClientError:
        return False
