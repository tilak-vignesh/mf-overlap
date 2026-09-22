import httpx

_HEADERS = {
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


class GrowwClient:
    """Thin client for Groww's public (unofficial) mutual fund JSON endpoints."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(headers=_HEADERS, timeout=15.0)
        self._primed = False

    async def _prime(self) -> None:
        if not self._primed:
            await self._client.get("https://groww.in/", headers={"referer": "https://groww.in/"})
            self._primed = True

    async def search_schemes(self, query: str, size: int = 6) -> list[dict]:
        """Fund name search. Returns candidates with 'title' and 'search_id' (slug),
        no holdings data."""
        await self._prime()
        resp = await self._client.get(
            "https://groww.in/v1/api/search/v3/query/global/st_p_query",
            params={
                "entity_type": "scheme",
                "is_us_stocks": "1",
                "page": "0",
                "query": query,
                "size": str(size),
                "web": "true",
            },
            headers={"referer": "https://groww.in/"},
        )
        resp.raise_for_status()
        return resp.json()["data"]["content"]

    async def scheme_detail(self, search_id: str) -> dict:
        """Full fund detail by slug, including the 'holdings' array with per-stock weights."""
        await self._prime()
        resp = await self._client.get(
            f"https://groww.in/v1/api/data/mf/web/v6/scheme/search/{search_id}",
            headers={"referer": f"https://groww.in/mutual-funds/{search_id}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def portfolio_stats(self, scheme_code: str, search_id: str) -> dict:
        """Aggregate portfolio stats by numeric scheme_code (from scheme_detail),
        including 'asset_allocation' (equity/debt/commodities/cash/... %) and
        'top_holdings'/'sector' breakdowns — NOT the same payload as scheme_detail,
        a genuinely separate endpoint. `search_id` is only used for the referer header."""
        await self._prime()
        resp = await self._client.get(
            f"https://groww.in/v1/api/data/mf/web/v1/scheme/portfolio/{scheme_code}/stats",
            headers={"referer": f"https://groww.in/mutual-funds/{search_id}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GrowwClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
