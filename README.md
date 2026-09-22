# ai-shi — Mutual Fund Overlap & Concentration Agent

An agentic system that takes a person's mutual fund holdings (fund names, or a
folio/consolidated statement), resolves the underlying stock holdings of each
fund, and calculates overlap between funds — flagging concentration risk (e.g.
the same large-cap stock showing up at high weight across "different" funds).

This is a research/analysis agent, not a trading agent. **No autonomous trade
or order execution anywhere in scope.**

## Architecture

```
Interactive CLI (app/cli.py) — the only interface
        |
Coordinator agent (app/agent/orchestrator.py) — owns the conversation with
the user (client-side history, held in-process by the CLI); routes questions
to domain specialists as tools, never answers fund questions itself
        |
Domain specialist(s) (app/agent/domains/) — self-contained: own prompt, own
tools, own one-shot tool-calling loop. Today: overlap_concentration.py
(fund overlap, portfolio concentration) and allocation_gap.py (current vs.
target equity/debt/gold split — report-only, never names specific funds
to buy)
        |
Tool layer (app/tools) — deterministic, each returns structured data or a
                          clear typed failure:
  - search_fund(query)             resolves fund name -> fund_id via live search
  - fetch_holdings(fund_id)        cache-first (once/day), falls back to scrapers
  - fetch_asset_allocation(fund_id) live equity/debt/commodity split (not cached)
  - scrapers/*                one scraper per data source (Groww implemented;
                               AMFI, Value Research, Moneycontrol stubs)
  - compute_overlap(a, b)    sum(min(weight_i, weight_j)) over common stocks
  - compute_concentration()  aggregate weighted exposure per stock across a
                              whole portfolio
```

Retry/fallback across data sources is a domain agent's judgment call —
`fetch_holdings` tries scrapers in order and reports success/failure/
incompleteness back to the agent, which decides whether to try the next
source or surface the failure to the user.

## Stack

- **CLI only** (`app/cli.py`, `rich` for terminal rendering) — no HTTP
  layer, no FastAPI
- **SQLite (WAL mode)**, async via `aiosqlite` — `funds`, `stocks`,
  `holdings_cache` tables (`app/models`). No user accounts / no per-user
  writes; the only writes are the shared daily holdings-cache refresh, so a
  single SQLite file in WAL mode (concurrent reads, serialized rare writes)
  is a better fit here than a Postgres server. See `design.md` for the
  full reasoning and the schema.
- **Prefect** — scheduled cache-refresh jobs (`app/jobs`)
- **Gemini API** (`google-genai`, model `gemini-3.8-flash`), hand-rolled
  tool-calling loop, coordinator + domain-specialist agents
  (`app/agent`) — no LangGraph/CrewAI for v1

## Data sourcing

No clean official real-time API for Indian MF holdings exists. Groww exposes
unofficial-but-usable JSON endpoints (fund search + a fund-detail endpoint
that includes a full weighted holdings list) and is the only implemented
scraper today. AMFI's monthly disclosure files, Value Research, and
Moneycontrol remain stubs. See `design.md` for the actual endpoints and the
quirks found while integrating them.

## Non-goals (v1)

- No trade execution / order placement
- No real-time/intraday data — the underlying disclosure is monthly-ish
  regardless of how often we check it
- No agent framework (LangGraph/CrewAI) — hand-roll the loop first
- No user accounts / no per-user persisted state
- No HTTP/API layer — CLI only

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # fill in DATABASE_URL, GEMINI_API_KEY
python -m app.cli
pytest
```

## Status

Working end-to-end, verified live: the CLI resolves fund names, fetches live
holdings/allocation data (Groww), computes overlap/concentration and
allocation-gap analysis, and Gemini narrates the result through the
coordinator → domain-specialist routing. Two domains exist so far:
overlap/concentration and allocation-gap. Conversation memory works across
turns within a CLI session.

Still stubs: AMFI/Value Research/Moneycontrol scrapers (only Groww is real),
the Prefect daily refresh job, and portfolio-side weighting (how much of the
user's money is in each fund — currently supplied per query, no
persistence). See `design.md` §4 for candidate next domains (goal planning,
fund performance, tax).
