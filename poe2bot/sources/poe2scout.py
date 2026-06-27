from __future__ import annotations
import aiohttp


class Poe2ScoutClient:
    def __init__(self, session: aiohttp.ClientSession, ua: str,
                 base: str = "https://api.poe2scout.com"):
        self._session = session
        self._ua = ua
        self._base = base.rstrip("/")

    def _headers(self) -> dict:
        return {"User-Agent": self._ua, "Accept": "application/json"}

    async def get_leagues(self) -> list[str]:
        async with self._session.get(f"{self._base}/leagues", headers=self._headers()) as r:
            r.raise_for_status()
            data = await r.json()
        # endpoint returns a list of {"value": <league name>}; tolerate bare strings too
        out = []
        for entry in data:
            out.append(entry["value"] if isinstance(entry, dict) else str(entry))
        return out

    async def get_currency_overview(self, league: str) -> dict:
        params = {"league": league}
        async with self._session.get(f"{self._base}/items/currency",
                                     headers=self._headers(), params=params) as r:
            r.raise_for_status()
            return await r.json()
