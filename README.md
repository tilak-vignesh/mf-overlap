# MF-Agent — Mutual Fund Overlap & Allocation Assistant

Terminal agent for Indian mutual fund portfolios: fund overlap, portfolio
concentration, and allocation-gap analysis. Research only — no trades, no
buy/sell recommendations, no naming specific funds.

## Features

- **Fund overlap** — % overlap in stock holdings between two funds + top shared holdings
- **Portfolio concentration** — true blended per-stock exposure across multiple funds
- **Allocation gap** — current equity/debt/gold split vs. a target split (report-only, no fund suggestions)
- Fuzzy fund name resolution (typos, missing "Direct Growth" suffix, etc.)

## Status

CLI only (`python -m app.cli`), local SQLite, no server, no deployment.
Tests: 16 passed, 5 skipped (skipped = hit live APIs, run manually).

Known gaps:
- Only Groww scraper is implemented; AMFI/Value Research/Moneycontrol are stubs
- `beautifulsoup4` dep is unused; `prefect` dep has only a stub job
- `scripts/seed_funds.py` is a stub — DB populates lazily on first use

## Architecture

```
CLI (app/cli.py)
  holds DB session + conversation history for the session
        │
Coordinator (app/agent/orchestrator.py)
  talks to the user, routes to specialists as tools, no fund logic itself
        │
Domain specialists (app/agent/domains/)
  overlap_concentration.py → search_fund, get_fund_overlap, get_portfolio_concentration
  allocation_gap.py        → search_fund, get_allocation_gap
  each: own prompt, own tools, own one-shot tool-calling loop
        │
Tools (app/tools/) — deterministic, no LLM
  search_fund, fetch_holdings(_many), fetch_asset_allocation(_many),
  compute_overlap, compute_concentration
        │
SQLite, WAL mode (app/db.py) — funds, stocks, holdings_cache
```

**Agent loop** (`app/agent/loop.py`): call model → dispatch any tool calls by
name → feed results back → repeat, capped at 8 iterations. Shared by
coordinator and both specialists via `app/agent/tool_registry.py`.

**Why specialists never ask questions**: only the coordinator holds
conversation history and talks to the user. Specialists run one-shot with no
reply channel, so they always answer fully and state assumptions instead of
asking. Coordinator is the only one that can end with a clarifying question.

**Concurrency**: independent network calls (Groww lookups) run concurrently
via `asyncio.gather`; DB reads/writes stay sequential on one shared
`AsyncSession` (SQLAlchemy: unsafe to share across coroutines). Pattern:
resolve what's cached (sequential) → fetch what's missing (concurrent) →
persist (sequential).

## Data model

```sql
funds (fund_id UUID PK, name, amc, isin UNIQUE NOT NULL)

stocks (stock_id UUID PK, isin UNIQUE NULL, ticker NULL,
        external_id UNIQUE NULL, name NOT NULL)

holdings_cache (
  fund_id, stock_id, as_of_date,   -- PK; as_of_date = source's disclosure date
  weight, source, fetched_at
)
```

`holdings_cache` rows are never deleted (free history). "Already checked
today" is based on `fetched_at`, not `as_of_date` — the source's disclosure
date can be weeks old regardless of fetch date. Asset allocation isn't
cached — always fetched live.

## Data source

Groww's unofficial JSON API (`app/tools/groww_client.py`), reverse-engineered:
- fund search (`st_p_query`)
- fund detail + holdings (`v6/scheme/search/{slug}`)
- portfolio stats + asset allocation (`v1/scheme/portfolio/{scheme_code}/stats` — separate endpoint, keyed by numeric scheme code)

Quirk: search returns nothing if the query includes "Direct Growth" etc. —
stripped before searching (`groww_lookup.py`).

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # DATABASE_URL, GEMINI_API_KEY
python -m app.cli
pytest
```

## Non-goals

- No trade execution, no buy/sell recommendations
- No real-time data — holdings checked at most once/day
- No agent framework (hand-rolled loop, on purpose)
- No accounts, no persisted per-user state, no HTTP/API layer
