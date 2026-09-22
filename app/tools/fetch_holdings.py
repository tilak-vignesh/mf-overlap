import asyncio
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fund import Fund
from app.models.holdings_cache import HoldingsCache
from app.tools.scrapers.amfi import AmfiScraper
from app.tools.scrapers.base import ScrapedHoldings
from app.tools.scrapers.groww import GrowwScraper
from app.tools.scrapers.moneycontrol import MoneycontrolScraper
from app.tools.scrapers.value_research import ValueResearchScraper
from app.tools.upsert import get_or_create_stock

# Tried in order until one succeeds. Groww is the only implemented source so
# far; the rest raise NotImplementedError, which counts as a failed attempt.
SCRAPERS = [GrowwScraper(), AmfiScraper(), ValueResearchScraper(), MoneycontrolScraper()]


async def fetch_holdings(fund_id: uuid.UUID, session: AsyncSession) -> list[HoldingsCache]:
    """Return today's cached holdings snapshot if we already have one;
    otherwise try each configured scraper in order until one succeeds,
    persist the result (keyed by the source's own disclosure date, not
    today's date), and return it. Raises RuntimeError with every source's
    failure reason if all sources fail. For more than one fund, prefer
    fetch_holdings_many — it fetches concurrently instead of one at a time."""
    results = await fetch_holdings_many([fund_id], session)
    return results[fund_id]


async def fetch_holdings_many(fund_ids: list[uuid.UUID], session: AsyncSession) -> dict[uuid.UUID, list[HoldingsCache]]:
    """Same contract as fetch_holdings, for several funds at once. Cache
    checks and DB writes run sequentially (they share one AsyncSession,
    which SQLAlchemy documents as unsafe for concurrent use) — but those are
    fast local SQLite operations. The actual scraping — the slow part, one
    or more live HTTP round-trips per fund — runs concurrently across every
    fund that needs a fresh fetch. Raises RuntimeError (from whichever fund
    failed first) if any fund's scrape fails — same failure contract as
    fetch_holdings, just applied per fund in the batch."""
    results: dict[uuid.UUID, list[HoldingsCache]] = {}
    to_scrape: list[uuid.UUID] = []
    fund_names: dict[uuid.UUID, str] = {}

    for fund_id in fund_ids:
        cached = await _get_todays_holdings(session, fund_id)
        if cached:
            results[fund_id] = cached
            continue
        fund = await session.get(Fund, fund_id)
        if fund is None:
            raise ValueError(f"no fund with id {fund_id}")
        fund_names[fund_id] = fund.name
        to_scrape.append(fund_id)

    if not to_scrape:
        return results

    scraped_results = await asyncio.gather(*(_scrape(fund_names[fid]) for fid in to_scrape), return_exceptions=True)

    for fund_id, scraped in zip(to_scrape, scraped_results):
        if isinstance(scraped, Exception):
            raise scraped
        results[fund_id] = await _persist_holdings(session, fund_id, scraped)

    return results


async def _scrape(fund_name: str) -> ScrapedHoldings:
    """Try each configured scraper in order until one succeeds. Pure
    network — touches no DB session, safe to run concurrently across funds."""
    failures: list[str] = []
    for scraper in SCRAPERS:
        try:
            result = await scraper.fetch(fund_name)
        except NotImplementedError:
            failures.append(f"{scraper.name}: not implemented")
            continue

        if isinstance(result, ScrapedHoldings):
            return result
        failures.append(f"{result.source}: {result.reason}")

    raise RuntimeError(f"could not fetch holdings for '{fund_name}': " + "; ".join(failures))


async def _get_todays_holdings(session: AsyncSession, fund_id: uuid.UUID) -> list[HoldingsCache]:
    """A cache hit means: we already checked this fund today. It does NOT mean
    the underlying disclosure changed today — sources publish on their own
    schedule — only that we don't need to re-check again until tomorrow."""
    result = await session.execute(
        select(HoldingsCache).where(
            HoldingsCache.fund_id == fund_id,
            HoldingsCache.fetched_at >= _start_of_today_utc(),
        )
    )
    return list(result.scalars())


def _start_of_today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def _persist_holdings(
    session: AsyncSession, fund_id: uuid.UUID, scraped: ScrapedHoldings
) -> list[HoldingsCache]:
    as_of = _parse_as_of_date(scraped.as_of_date)
    fetched_at = datetime.now(timezone.utc)

    rows = []
    for h in scraped.holdings:
        stock = await get_or_create_stock(session, external_id=h.external_id, name=h.name)
        rows.append(
            HoldingsCache(
                fund_id=fund_id,
                stock_id=stock.stock_id,
                as_of_date=as_of,
                weight=h.weight,
                source=scraped.source,
                fetched_at=fetched_at,
            )
        )
    for row in rows:
        await session.merge(row)
    await session.flush()
    return rows


def _parse_as_of_date(raw: str) -> date:
    """Parses the source's disclosure date, e.g. '2026-08-30T18:30:00.000Z'
    (Groww's portfolio_date)."""
    if not raw:
        return date.today()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()
