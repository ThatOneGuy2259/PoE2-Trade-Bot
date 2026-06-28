from __future__ import annotations
import asyncio
from urllib.parse import quote
import aiohttp
import yarl


class Poe2ScoutClient:
    """Client for the poe2scout public API (https://api.poe2scout.com).

    Endpoints (verified against the live API):
      - Leagues:    GET /{realm}/Leagues            -> bare array of league objects
      - Currencies: GET /{realm}/Leagues/{name}/Currencies/ByCategory?Category=currency
                    -> {CurrentPage, Pages, Total, Items:[...]} (paginated)
    Field names are PascalCase (Value, Items, ApiId, CurrentPrice, CurrentQuantity).
    """

    def __init__(self, session: aiohttp.ClientSession, ua: str,
                 base: str = "https://api.poe2scout.com", realm: str = "poe2",
                 req_delay_s: float = 0.4):
        self._session = session
        self._ua = ua
        self._base = base.rstrip("/")
        self._realm = realm
        # Courtesy delay before each request so a multi-category poll (one fetch per category)
        # stays within poe2scout's ~2 req/s etiquette. Tests pass req_delay_s=0.
        self._req_delay_s = req_delay_s

    def _headers(self) -> dict:
        return {"User-Agent": self._ua, "Accept": "application/json"}

    async def get_leagues_meta(self) -> list[dict]:
        """Full league objects (Value, IsCurrent, DivinePrice, ChaosDivinePrice, ...)."""
        url = f"{self._base}/{self._realm}/Leagues"
        async with self._session.get(url, headers=self._headers()) as r:
            r.raise_for_status()
            return await r.json()

    async def get_leagues(self) -> list[str]:
        """League names (the `Value` field), current league(s) first for autocomplete."""
        meta = await self.get_leagues_meta()
        names = [e["Value"] for e in meta if isinstance(e, dict) and e.get("Value")]
        current = {e.get("Value") for e in meta if isinstance(e, dict) and e.get("IsCurrent")}
        return [n for n in names if n in current] + [n for n in names if n not in current]

    async def get_current_league(self) -> str | None:
        meta = await self.get_leagues_meta()
        for e in meta:
            if isinstance(e, dict) and e.get("IsCurrent"):
                return e.get("Value")
        return meta[0].get("Value") if meta and isinstance(meta[0], dict) else None

    async def get_league_meta(self, league: str) -> dict | None:
        for e in await self.get_leagues_meta():
            if isinstance(e, dict) and e.get("Value") == league:
                return e
        return None

    async def get_currency_overview(self, league: str, category: str = "currency",
                                    per_page: int = 250) -> dict:
        """All items in one currency-family category for a league, paging through
        Currencies/ByCategory.

        Returns {"Items": [...all pages...], "Pages": n, "Total": t}. Both the league name
        and category go in pre-encoded URL parts so yarl does not double-encode them.
        """
        path = f"{self._base}/{self._realm}/Leagues/{quote(league, safe='')}/Currencies/ByCategory"
        cat = quote(category, safe='')
        items: list[dict] = []
        page, pages, total = 1, 1, 0
        while True:
            qs = f"Category={cat}&PerPage={per_page}&Page={page}"
            url = yarl.URL(f"{path}?{qs}", encoded=True)
            await asyncio.sleep(self._req_delay_s)         # rate-limit courtesy
            async with self._session.get(url, headers=self._headers()) as r:
                r.raise_for_status()
                data = await r.json()
            items.extend(data.get("Items", []))
            pages = data.get("Pages", 1) or 1
            total = data.get("Total", len(items))
            if page >= pages:
                break
            page += 1
        return {"Items": items, "Pages": pages, "Total": total}
