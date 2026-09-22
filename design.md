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

### 2.5 Provider: Gemini, not Claude — switched mid-build

Originally built against the Anthropic SDK (`claude-opus-5`); switched to
Google's `google-genai` SDK (`gemini-3.8-flash`) per explicit request. Key
`GEMINI_API_KEY` from `.env` via `Settings`, not the SDK's own env
auto-detection (pydantic-settings loads `.env` into the `Settings` object,
not into `os.environ`). Tool schemas are defined provider-neutrally
(`{name, description, parameters}` plain JSON schema, no Anthropic-specific
`input_schema`/`strict` fields) so the same tool definitions work under
either provider's SDK adapter.

### 2.6 Conversation memory — client-owned, server stays stateless

The agent kept proposing follow-up questions the user had no channel to
answer (the API was single-shot request/response). Two options: server-owned
sessions (a new DB table, session_id, expiry/cleanup — a materially
different, more frequent write pattern than the daily fund-cache writes
§2.4 sized SQLite around) vs. client-owned history (caller sends the full
transcript each call, server stays fully stateless). Chose **client-owned
history** — `run_agent(user_message, session, history=None) -> (reply,
updated_history)`; the caller stores and resends `history` to continue a
conversation, or omits it for a fresh one-shot query. No new DB table, no
session state — doesn't reopen the "no per-user persisted state" decision
in §2.4 at all. Verified live: a follow-up referencing "those two funds"
from a prior turn resolved correctly without re-stating fund names.

### 2.7 Multi-agent split: coordinator + domain specialists

Initially built as one agent (one system prompt, one flat tool list) — the
right default for a small, single-domain tool surface (see the
Claude-vs-LangGraph/CrewAI discussion: no framework needed when there's no
real branching or multi-persona need). As the roadmap grew to include
domains beyond overlap/concentration (goal planning, fund performance, tax,
risk profiling — see §4), explicitly restructured **before** adding the
second domain, to avoid a monolith-refactor later:

- **Coordinator** (`app/agent/orchestrator.py`) — the only agent the
  outside world (API/CLI) talks to. Owns the actual multi-turn conversation
  and `history` (§2.6). Its tools are *other agents*, not deterministic
  functions — currently one: `consult_overlap_agent`.
- **Domain specialist** (`app/agent/domains/overlap_concentration.py`) — a
  self-contained module: own system prompt, own tools
  (`search_fund`/`get_fund_overlap`/`get_portfolio_concentration`), own
  one-shot `run(query, session) -> str`. Invoked *by* the coordinator, never
  called directly by the user.
- **Shared plumbing**, extracted so both levels (and every future domain)
  reuse it rather than duplicating: `app/agent/loop.py` (`run_tool_loop` —
  the actual hand-rolled `while`-style loop) and `app/agent/tool_registry.py`
  (`to_gemini_tools`, `make_dispatcher` — generic helpers, no longer
  overlap-specific despite the filename).

**A real design correction made while building this, not obvious in
advance:** the domain specialist has no direct channel to the user — the
coordinator does. So the "ask a clarifying question" behavior from §2.6
can't live in the domain's prompt (it has no way to relay a question
anywhere) — it always gives its fullest answer with stated assumptions.
The coordinator's prompt is the one that still carries "ask a clarifying
question when it matters," since it's the one actually holding the
conversation.

**Deliberately not built yet:** an LLM-based routing decision. With exactly
one domain, "routing" has no real decision to make — an LLM call to decide
that would be latency/cost for a foregone conclusion. What's built now is
the *structural* split (self-contained domain modules, a coordinator that
calls them as tools) so the next domain is "write a new module + register
one more coordinator tool," not a refactor of what already works.

Verified live end-to-end through this restructure: same 28.25% overlap
answer as before, now visibly routed through `consult_overlap_agent` (the
domain specialist's own tool-calling loop runs inside the coordinator's
tool call). At the time, both a FastAPI HTTP endpoint and the CLI worked
against this unchanged, since `run_agent()`'s public signature didn't
change — the FastAPI layer was removed shortly after, see §2.8.

### 2.8 FastAPI removed — CLI is the only interface

`app/main.py` and `app/api/` (the `GET /funds/search` and
`POST /portfolio/analyze` HTTP routes) deleted, along with the `fastapi`
and `uvicorn` dependencies. `pydantic` stays installed transitively via
`pydantic-settings` (still used for `.env`/`Settings`) but is no longer a
direct dependency — nothing imports it directly anymore. `app/cli.py` was
already a complete, working interface calling `run_agent()` directly with
zero HTTP involved (§2.7's restructure made a point of not touching
`run_agent()`'s signature specifically so both interfaces kept working
unchanged) — removing the HTTP layer was a pure deletion, not a rewrite.
Verified after removal: CLI still imports and runs correctly, full test
suite still passes (11 passed, 4 skipped — none of the tests depended on
FastAPI's `TestClient`).

### 2.9 Second domain: allocation-gap — report-only, no fund suggestions

The user proposed: ask for a target equity/debt/gold split and risk
tolerance, compute the current blended split from their actual funds, and
suggest new funds to close the gap. Agreed on the first half; the second
half (naming specific funds to buy) was deliberately scoped **out** —
that's investment advice, not analysis, and conflicts with the project's
own non-goals (§5) and the "no buy/sell recommendations" rule already in
every agent's prompt. What got built: report the gap, and at most describe
what *category* of fund would close it ("a debt fund," "a gold ETF/FoF") —
never a specific fund name or ticker. This boundary is written directly
into the domain's system prompt in `app/agent/domains/allocation_gap.py`,
not left to be inferred.

Structurally, this is the second instance of the §2.7 pattern: a new
self-contained module (own prompt, own tools: `search_fund` again — each
domain resolves fund names independently, no cross-domain state sharing —
plus `get_allocation_gap`), registered as one more coordinator tool
(`consult_allocation_gap_agent`). No changes needed to the coordinator's
core loop, `overlap_concentration.py`, or any shared plumbing beyond adding
the new tool entry — exactly the "not a refactor" claim from §2.7 held up
in practice.

**A real bug found and fixed while building this, not obvious in
advance:** the plan was to read `asset_allocation` off the same
`scheme_detail` payload `fetch_holdings` already pulls holdings from — a
field I recalled seeing in this payload earlier in the build. Live-checked
before trusting that recollection: `scheme_detail`'s actual key list has no
`asset_allocation` field at all (confirmed by printing every key). The
field only exists on a *separate* endpoint,
`GET .../v1/api/data/mf/web/v1/scheme/portfolio/{scheme_code}/stats`
(keyed by the fund's numeric `scheme_code`, not its slug) — the same
endpoint explored right at the start of this build (§2.1) and then
forgotten when this feature was designed. First live test of the new
domain caught this immediately: it returned confident-sounding but
entirely fabricated "~65-75% equity" range estimates instead of real
numbers, because the underlying tool call was silently getting back all
zeros and the model was covering for missing data with background
knowledge about what these fund *categories* typically hold. Fixed by
adding `GrowwClient.portfolio_stats()` and
`groww_lookup.find_asset_allocation()` (a genuinely separate lookup
path, factored to share the search+fuzzy-match step with
`find_scheme_detail()` via `_find_best_candidate()`). Re-verified live
after the fix: real numbers matching the exact values confirmed against
the raw API earlier (Parag Parikh Flexi Cap: 82.24% equity, 6.26% debt) —
not estimates.

**Also confirmed, still holding:** Groww has no dedicated "gold" field —
`commodities` (a broader bucket) is the only proxy, and both the tool
output and the domain's prompt say so explicitly rather than presenting it
as a precise gold-only figure.

## 3. System design (implemented as of this writing)

### 3.1 Components

```
Interactive CLI (app/cli.py) — the only interface (§2.8: FastAPI removed)
        |
        v  direct in-process call, history held by the CLI loop
Coordinator agent (app/agent/orchestrator.py) — owns the conversation with
the user (client-owned `history`, §2.6); routes to domain specialists as
tools, never answers fund questions itself.
        |
        v
Domain specialist(s) (app/agent/domains/) — self-contained: own prompt, own
tools, own one-shot loop. Today: overlap_concentration.py and
allocation_gap.py (§2.9) — see §4 for what's next.
        |
        v
Tool layer (app/tools), each returns structured data or a typed failure:
  - search_fund(query)             Groww name search -> upsert into
                                    `funds`, ranked (fund_id, name, score)
  - fetch_holdings(fund_id)        day-scoped cache check -> scraper
                                    fallback chain -> persist -> return
  - fetch_asset_allocation(fund_id) live (not cached), equity/debt/
                                    commodity/other split — separate Groww
                                    endpoint from fetch_holdings (§2.9)
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
- **Agent orchestrator/coordinator/domain split — done, verified live.**
  See §2.5 (Gemini switch), §2.6 (conversation memory), §2.7 (multi-agent
  restructure) for the full history. Current state: `app/agent/orchestrator.py`
  is the coordinator (owns the conversation, routes via tools-as-agents);
  `app/agent/domains/overlap_concentration.py` is the one domain specialist
  so far, exposing `search_fund` / `get_fund_overlap` /
  `get_portfolio_concentration` to itself.

  **Why `get_fund_overlap`/`get_portfolio_concentration` are composed tools**
  instead of exposing `fetch_holdings`/`compute_*` directly to the model:
  those return/require raw `{stock_id: weight}` dicts of 60-300+ entries per
  fund — shuttling that through model context would burn tokens for data
  the model never needs to see; it only needs final percentages and a
  handful of named contributors to narrate from. The underlying
  deterministic `compute_overlap`/`compute_concentration` in `app/tools/`
  are unchanged and still directly unit-tested.

  Verified fully live (not just structurally): real Gemini responses
  through the coordinator via the CLI, correct 28.25% overlap number,
  multi-turn follow-ups resolving correctly using client-owned `history`,
  and the coordinator→domain routing confirmed by inspecting the actual
  answer content.
- **Further domains beyond overlap/concentration and allocation-gap (§2.9)**
  — not yet built. Candidates still on the table: goal planning / SIP
  projection (deterministic math, no new data source), fund performance &
  quality (needs historical NAV time series — this is where TimescaleDB,
  mentioned as optional in §1, would actually become relevant), tax
  awareness (India-specific: LTCG/STCG, ELSS lock-in, direct-vs-regular
  expense drag). Allocation-gap already covers the "risk profiling via
  target split" idea, report-only per §2.9's scope decision.
- **Portfolio-side weighting** (how much of the *user's* money is in each
  fund, as opposed to each fund's internal stock weights). Discussed
  separately: no schema for this yet. Given the "no accounts, no per-user
  state" decision above (2.4), this will need to be supplied by the user in
  their CLI message each time rather than persisted — consistent with the
  stateless design, but not yet designed in detail.
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
