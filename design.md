# Design: Mutual Fund Overlap Agent

Living document. Captures the decisions made so far (with the evidence behind
them) and the current system design. Update this as decisions change instead
of relying on chat history.

## 1. Goal (unchanged from the original brief)

Take a user's mutual fund holdings (fund names, or a folio/consolidated
statement), resolve each fund's underlying stock holdings, and compute
overlap / concentration risk across the portfolio. Research/analysis only —
no trade execution, no order placement, no accounts, no per-user state.

## 2. Decisions made so far

### 2.1 Data source: Groww's unofficial JSON API, not Playwright, not AMFI (for now)

We initially planned to scrape with Playwright. In practice, Groww exposes
plain JSON endpoints that work with a stateless `httpx` client — no browser
needed. Verified live against real funds during this build:

- **Fund name search** (resolves an ambiguous name to a fund slug):
  ```
  GET https://groww.in/v1/api/search/v3/query/global/st_p_query
      ?entity_type=scheme&is_us_stocks=1&page=0&query={name}&size=6&web=true
  Headers: referer: https://groww.in/
  ```
  Requires one throwaway `GET https://groww.in/` first to pick up a session
  cookie. Returns `data.content[]` items with `title` and `search_id` (slug).

  **Known quirk (confirmed, not theoretical):** a query that includes the
  plan/option suffix (e.g. `"... Direct Growth"`) reliably returns zero
  results, even though the fund exists — `app/tools/scrapers/groww.py`
  strips that suffix (`_search_query`) before searching.

- **Fund detail + holdings**:
  ```
  GET https://groww.in/v1/api/data/mf/web/v6/scheme/search/{search_id}
  ```
  Returns fund metadata (`scheme_name`, `isin`, `nav_date`, `fund_house`, ...)
  and a `holdings` array — one entry per underlying stock:
  ```json
  {
    "company_name": "HDFC Bank Ltd",
    "stock_search_id": "hdfc-bank-ltd",
    "sector_name": "Financial",
    "corpus_per": 7.63470687,
    "portfolio_date": "2026-08-30T18:30:00.000Z"
  }
  ```
  `corpus_per` is the weight (% of fund AUM) — this is the number that feeds
  `compute_overlap` / `compute_concentration`. Verified end-to-end: Parag
  Parikh Flexi Cap Fund returned 150 holdings, HDFC Balanced Advantage Fund
  returned 274; `compute_overlap` on the two real holdings sets gave 28.25%.

- **We evaluated a free/open-source alternative** (`finstacklabs/finstack-mcp`'s
  `get_mf_overlap`) and rejected it: its own source comments admit the AMFI
  API it calls (`mfapi.in`) only returns NAV history, not portfolio holdings.
  It falls back to a hardcoded dictionary of top-10 holdings for ~10 major
  funds and computes overlap via plain set intersection, ignoring weights
  entirely. Not usable as a real data source.

- **No forward-looking disclosure schedule exists in the API.** Checked
  every field in the fund detail payload for anything date/schedule-shaped —
  only `allotment_date`, `launch_date`, and `nav_date` exist at the top
  level, plus each holding's own `portfolio_date`. There is no "next
  disclosure" field. Consequence: the system cannot proactively know when a
  fund's holdings will next update — it can only reactively check whether
  what it has is stale each time it's asked.

- AMFI (official static monthly disclosure files) and the other scrapers
  (Value Research, Moneycontrol) remain **stubs** — only Groww is
  implemented. `fetch_holdings` already tries scrapers in a configured
  order and treats `NotImplementedError` as a failed attempt, so adding a
  real second source later is a drop-in.

### 2.2 Bug found while tracing the caching logic: `as_of_date` uses the wrong date

`app/tools/scrapers/groww.py` currently sets
`ScrapedHoldings.as_of_date = detail.get("nav_date")` — the fund's daily NAV
date — not `portfolio_date`, the date the underlying holdings composition
was actually disclosed. Confirmed from a real response: `nav_date` was
`21-Sep-2026` while the holdings' own `portfolio_date` was `2026-08-30`, a
~3-week gap. Storing `nav_date` as `as_of_date` makes a month-old holdings
snapshot look like it was captured yesterday.

**Decision:** use `portfolio_date` (from the holdings entries) as the
canonical `as_of_date`, not `nav_date`.

### 2.3 Move from a rolling staleness window to a day-scoped cache

Original design: `fetch_holdings` treated any snapshot with
`as_of_date >= today - HOLDINGS_STALE_AFTER_DAYS` as fresh. Problem found by
tracing it: this returns *every* row in that window, not just the latest
snapshot — if two different `as_of_date`s ever land inside the window, stock
weights get double-counted in `compute_overlap`/`compute_concentration`.

**Decision:** stop reasoning about "within N days." Cache is scoped to the
calendar day: a fund's holdings are refreshed once per day, keyed by
`(fund_id, stock_id, date)`. "Fresh" = "there's already a row for today's
date"; anything else triggers a re-scrape. Old dated rows are **not**
deleted — keeping them costs almost nothing and gives free
concentration-drift-over-time later, without needing a separate history
mechanism.

### 2.4 PostgreSQL → SQLite

Reasoning, in order:
1. The app is stateless from the user's side — no accounts, no sign-in, no
   per-user writes, ever.
2. The only writes are the shared daily holdings-cache refresh: one writer,
   rare (once per fund per day), idempotent.
3. All browser/client traffic is reads against that shared cache.
4. This — rare writes, heavy concurrent reads — is SQLite's actual sweet
   spot, provided it runs in **WAL mode** (`PRAGMA journal_mode=WAL`), so
   reads aren't blocked by the occasional write.

**Decision:** SQLite (WAL mode), `aiosqlite` as the async driver (replacing
`asyncpg`). Alembic and TimescaleDB drop out of scope — neither is needed
for a single SQLite file; if the schema needs to change later, a plain
migration script is enough at this scale.

**Caveat, stated plainly:** this stops being the right call the moment this
needs multiple independent write paths (e.g. a horizontally-scaled backend
with several processes hammering writes concurrently) — SQLite serializes
writers. Given writes are ~1/fund/day, that's not a real constraint here,
but it's the thing that would force a revisit.

## 3. System design (implemented as of this writing)

### 3.1 Components

```
Browser clients (no auth, no accounts)
        |
        v  read-mostly HTTP
FastAPI app (app/main.py)
  - GET /funds/search?query=...        -> search_fund
  - POST /portfolio/analyze             -> agent orchestrator
        |
        v
Agent orchestrator (app/agent) — hand-rolled Anthropic tool-calling loop.
Decides what to fetch/match/compute; delegates deterministic work to tools.
        |
        v
Tool layer (app/tools), each returns structured data or a typed failure:
  - search_fund(query)         Groww name search -> upsert into `funds`,
                                ranked (fund_id, name, score)
  - fetch_holdings(fund_id)    day-scoped cache check -> scraper fallback
                                chain -> persist -> return
  - scrapers/groww.py          IMPLEMENTED — Groww JSON endpoints
  - scrapers/amfi.py           stub
  - scrapers/value_research.py stub
  - scrapers/moneycontrol.py   stub
  - compute_overlap(a, b)      deterministic: sum(min(w_i, w_j))
  - compute_concentration()    deterministic: weighted exposure per stock
        |
        v
SQLite (WAL mode) — single file, shared, no per-user rows
  - funds, stocks, holdings_cache (see 3.2)
        |
        v
Prefect flow (app/jobs/refresh_cache.py) — daily background refresh of
funds already in the cache, so a user's request doesn't always pay the
scrape latency for popular funds.
```

### 3.2 Schema (SQLite, target state)

```sql
funds (
  fund_id     TEXT PRIMARY KEY,   -- uuid, stored as text
  name        TEXT NOT NULL,
  amc         TEXT NOT NULL,
  isin        TEXT UNIQUE NOT NULL
)

stocks (
  stock_id     TEXT PRIMARY KEY,
  isin         TEXT UNIQUE,        -- nullable: source doesn't always give it
  ticker       TEXT,               -- nullable: same reason
  external_id  TEXT UNIQUE,        -- Groww's stock_search_id (slug); the
                                    -- practical dedupe key today
  name         TEXT NOT NULL
)

holdings_cache (
  fund_id       TEXT NOT NULL REFERENCES funds(fund_id),
  stock_id      TEXT NOT NULL REFERENCES stocks(stock_id),
  as_of_date    DATE NOT NULL,     -- from the source's portfolio_date, not nav_date
  weight        NUMERIC NOT NULL,  -- % of fund AUM
  source        TEXT NOT NULL,     -- which scraper populated this row
  fetched_at    TIMESTAMP NOT NULL,
  PRIMARY KEY (fund_id, stock_id, as_of_date)
)
```

`overlap_cache` (originally proposed) is dropped from the target schema —
`compute_overlap` is cheap enough over a day-scoped holdings set that
caching its result isn't worth the staleness-tracking cost. Revisit only if
profiling says otherwise.

### 3.3 Fetch/cache flow (`fetch_holdings`, implemented behavior)

**Correction from an earlier draft of this doc:** "cache hit" cannot mean
`as_of_date == today` — `as_of_date` is the source's disclosure date (e.g.
Aug 30), which almost never equals today given the weeks-long lag described
in 2.2. What "day-scoped" actually means is: **we only check the source
once per calendar day**, tracked via `fetched_at`, independent of whatever
`as_of_date` the disclosure itself carries.

1. Query `holdings_cache` for `(fund_id, fetched_at >= start of today UTC)`.
2. If rows exist → return them (cache hit, no network call — we already
   checked this fund today, whatever the disclosure date turned out to be).
3. If not → try scrapers in configured order (currently: Groww only real;
   others are stubs that count as failed attempts) until one returns
   `ScrapedHoldings`.
4. On success: upsert `stocks` rows (by `external_id`), `session.merge()`
   `holdings_cache` rows keyed by `(fund_id, stock_id, portfolio_date)` —
   **not** today's date, the source's actual as-of date (see 2.2) — and
   return them.
5. `merge()` means if `portfolio_date` happens to equal an existing row's
   date (the source hasn't published a new disclosure since last check),
   the row is updated in place, not duplicated.
6. If every scraper fails: raise with every source's failure reason
   attached.

### 3.4 What changed in the codebase (done)

- `app/db.py`: swap `asyncpg` → `aiosqlite`, enable WAL mode on connect.
- `app/models/holding.py`: rename table `holdings` → `holdings_cache`
  (behavior change, not just naming — see 3.3); drop the
  `HOLDINGS_STALE_AFTER_DAYS`-window query in favor of the day-scoped one.
- `app/tools/fetch_holdings.py`: rewrite `_get_fresh_holdings` to check for
  today's date specifically, not a rolling window.
- `app/tools/scrapers/groww.py`: change `as_of_date` source from `nav_date`
  to each holding's own `portfolio_date`.
- `app/models/overlap_cache.py`: remove (see 3.2).
- `alembic.ini`, `migrations/`: remove — not needed for SQLite at this scale.
- `pyproject.toml`: drop `asyncpg`, `alembic`; add `aiosqlite`.
- `.env.example`: `DATABASE_URL` becomes a SQLite path, e.g.
  `sqlite+aiosqlite:///./ai_shi.db`.

## 4. Still open / not decided yet

- **AMFI as a second real scraper.** Still a stub. Worth doing next since
  it's the brief's originally-preferred primary source and gives us a real
  fallback (currently `fetch_holdings` has exactly one working source).
- **Agent orchestrator — done.** `app/agent/orchestrator.py` is a hand-rolled
  `while`-style loop (manual, no agent framework — per §2 of the original
  brief). It exposes three tools to the model (`app/agent/tool_registry.py`
  + `app/agent/tools.py`), defined provider-neutrally as
  `{name, description, parameters}` (plain JSON schema):
  - `search_fund(query)` — thin wrapper over the existing tool
  - `get_fund_overlap(fund_id_a, fund_id_b)` — internally calls
    `fetch_holdings` for both funds + `compute_overlap`, returns the overlap
    % plus the top 5 shared holdings for narrative color
  - `get_portfolio_concentration(fund_ids, weights)` — internally calls
    `fetch_holdings` per fund + `compute_concentration`, returns the top 10
    stock exposures

  **Why composed tools instead of exposing `fetch_holdings`/`compute_*`
  directly to the model:** those return/require raw `{stock_id: weight}`
  dicts of 60-300+ entries per fund — shuttling that through model context
  as tool input/output would burn tokens for data the model never needs to
  see; it only needs the final percentages and a handful of named
  contributors to narrate from. The underlying deterministic
  `compute_overlap`/`compute_concentration` in `app/tools/` are unchanged
  and still directly unit-tested.

  **Model provider: Gemini, not Claude — switched mid-build.** Originally
  built against the Anthropic SDK (`claude-opus-5`, manual loop per
  `tool_use`/`tool_result` blocks); switched to Google's `google-genai` SDK
  (`gemini-3.8-flash`, chosen because Google's own docs describe it as
  "engineered for long-horizon software engineering, autonomous agents" —
  a better fit than the `-pro-preview` tier, which is a preview model) per
  explicit request, with the key read via `Settings.gemini_api_key` from
  `.env` (not the SDK's own env auto-detection, since pydantic-settings
  loads `.env` into the `Settings` object, not into `os.environ`). The
  provider swap only touched `app/agent/orchestrator.py` and the schema
  shape in `tool_registry.py` (Anthropic's `input_schema`/`strict` fields
  → a plain `parameters` JSON-schema dict, since Gemini's SDK takes a
  differently-shaped tool definition) — `app/agent/tools.py` (the actual
  tool logic) and everything below it were untouched, since `dispatch()`
  was already provider-agnostic.

  Verified structurally end-to-end via FastAPI's `TestClient` (no live
  Gemini credentials in this environment): `POST /portfolio/analyze`
  correctly reaches the orchestrator, builds the request, and fails only at
  the SDK's own client-construction step (`genai.Client(api_key=...)`
  raises `ValueError: No API key was provided` since `GEMINI_API_KEY` isn't
  set here) — routing, request parsing, DB session injection, and message
  construction all confirmed working. **Not yet verified against a live
  model response** — run it yourself with a real key to confirm the full
  tool-calling round trip and the narrative output quality.
- **Portfolio-side weighting** (how much of the *user's* money is in each
  fund, as opposed to each fund's internal stock weights). Discussed
  separately: no schema for this yet. Given the "no accounts, no per-user
  state" decision above (2.4), this will need to be supplied by the caller
  per-request (e.g. in the `POST /portfolio/analyze` body) rather than
  persisted — consistent with the stateless design, but not yet designed
  in detail.
- **Prefect daily refresh job** (`app/jobs/refresh_cache.py`): still a
  stub; needs to decide which funds to proactively refresh (all funds ever
  requested? a fixed popular-funds list?).

## 5. Non-goals (carried over, unchanged)

- No trade execution, no order placement
- No real-time/intraday data — the source data itself is still whatever the
  fund discloses (roughly monthly); "day-scoped cache" refers to how often
  *we* re-check, not how often the underlying disclosure actually changes
- No agent framework (LangGraph/CrewAI) — hand-roll the loop first
- No user accounts / no per-user persisted state
