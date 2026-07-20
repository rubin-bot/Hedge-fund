# Project Summary — read this first in any new session

Bridge document for picking up this project cold. For behavioral
conventions/policy (data source rules, scraper conventions, factor/portfolio/
risk conventions), read `CLAUDE.md` too — that's the authoritative, actively
maintained policy file; this document is a point-in-time state snapshot
(as of 2026-07-20) and can go stale, so verify against the actual code
before relying on specifics here.

## What this is

A Python quantitative long-short equity research system: data ingestion →
factor scoring → AI filing/transcript analysis → a virtual cash ledger
with a daily decide-and-track loop → a FastAPI + Next.js reporting layer.
Free-tier-first data sourcing, Gemini for all LLM calls (see `CLAUDE.md`).

**The full pipeline is built and tested end to end (95 passing Python
tests + a clean Next.js typecheck/lint/build).** The old Streamlit
`dashboard/app.py` skeleton and the old weight-based `PaperTradingEngine`
(`simulation/paper_trading.py`) are both superseded — see below — and can
be deleted whenever someone gets around to it; nothing depends on them.

## Where this session left off / what to do next

- **Repo is on GitHub**: `https://github.com/rubin-bot/Hedge-fund` (public,
  account `rubin-bot`). Verify local `master` vs `origin/master` fresh each
  session — this doc doesn't track push state turn to turn.
- **This session reworked paper trading into a manual daily
  decide-and-track loop**, replacing the automated MVO-rebalance engine
  from the prior session. The interaction model is now: the app surfaces
  ranked candidates from the model each day, you read the AI Analysis for
  a pick, you choose which to execute and how much virtual cash to put
  behind each, and the system tracks that specific position (open → held →
  closed) against a virtual cash ledger, reconciling daily against real
  closing prices and SPY.
  - `simulation/virtual_ledger.py` — the core of the new loop:
    `deposit()`, `get_balance()` (always DERIVED from `cash_deposits` +
    `positions`, never a stored running total), `execute_candidate()`
    (guards against a double-click overspend race via `BEGIN IMMEDIATE`),
    `close_position()`, `run_end_of_day()` (per-open-position mark vs.
    SPY + one account-level snapshot, both upsert-safe for re-running the
    same date), `circuit_breaker_status()` (reuses
    `risk/circuit_breaker.py` verbatim, built from investment P&L only —
    NOT `total_account_value`, so a deposit can never look like a fake
    "return" and corrupt the drawdown check), `reset()`. Four new tables
    in `data/db.py`'s `SCHEMA`: `cash_deposits`, `positions`,
    `position_daily_marks`, `account_daily_snapshots`.
  - `api/routers/account.py` (deposit/balance/reset/run-end-of-day/
    overview), `api/routers/candidates.py` (today's ranked longs/shorts —
    bypasses MVO entirely, just `ScoringEngine` + `select_long_short_candidates`
    — plus the on-demand, cached "AI Analysis" panel per candidate),
    `api/routers/positions.py` (execute/close/list — the new Simulated
    Execution Log source) are all new. `api/routers/risk.py` and
    `api/routers/performance.py` were reworked (via the new
    `api/ledger_service.py`) to analyze the user's actual open positions
    instead of a hypothetical MVO target book. The old
    `/api/portfolio/current` and `/api/execution/trades` routes are gone
    — their roles are now split across `candidates`/`positions`/`account`.
  - **AI Analysis panel**: reuses `FilingStructureAnalyzer`,
    `RiskFactorAnalyzer`, `InsiderTransactionAnalyzer` completely
    unmodified, populating their `sec_filings`/`sec_form4_transactions`
    dependencies on-demand per ticker on first expand
    (`data/backfill.py`'s `backfill_sec_filings`/`backfill_form4`, both
    now take `include_history`/`limit` params added this session — the
    CLI backfill's full-history default is far too slow for an
    interactive click, e.g. it never finished for one ticker in 4+
    minutes before the fix; `include_history=False` +
    `FORM4_FETCH_LIMIT=20` in `candidates.py` bounds first-expand latency
    to under a minute). `TranscriptSentimentAnalyzer` is never called —
    always reported "unavailable" (no free transcript source exists, a
    documented gap from before this session).
  - `web/`'s Portfolio view (`/`) was rebuilt around the new loop: cash
    balance + breakdown, Deposit Funds control, today's candidates with
    an AI Analysis expander and an Execute (cash-amount) control per
    card, open positions with live P&L and a Close button, plus Run End
    of Day / Reset Account buttons. Execution Log (`/execution`) now
    lists positions (entry → exit) instead of a flat fill log. Risk
    Controls (`/risk`) now analyzes real open positions. Factor Research
    (`/factors`) and the AI Commentary view (`/ai`) are otherwise
    unchanged from last session, just re-pointed at the new data sources
    where relevant.
  - `web/src/lib/types.ts`/`api.ts` were fully rewritten to match — no
    leftover references to the old portfolio/execution shapes.
- **The DB was fully wiped and re-verified empty this session**: run
  `curl -X POST http://localhost:8000/api/account/reset` (or
  `simulation.virtual_ledger.reset()` directly) any time you want to
  return to a clean-slate account. This also clears the OLD
  `paper_trades`/`paper_portfolio_snapshots` tables (from the prior
  session's now-superseded seeded data) since they're fully replaced by
  the tables above — it does **not** touch `ai_analysis_cache`,
  `sec_filings`, or `sec_form4_transactions`, which are reusable market/
  analysis data, not trading state.

## Location

Git repo root: `C:\Users\RUBIN\Desktop\Projects_Cursor\Cludi\Hedge fund\`.
The sibling folder `Cludi\Test1\` is a **separate, unrelated** snake-game
project — never touch it, never assume its files are related.

## Directory map (only non-empty/meaningful files listed)

```
Hedge fund/
├── CLAUDE.md                 # policy: data sources, scraper/factor/portfolio/risk conventions — READ THIS
├── README.md                 # architecture explanation + setup instructions (a stale early scaffold doc —
│                               # e.g. it still numbers factors/ as a single definitions.py; CLAUDE.md/this file are authoritative)
├── PROJECT_SUMMARY.md         # this file
├── .env / .env.example        # API keys (GOOGLE_API_KEY, QUIVERQUANT_API_KEY,
│                               # FRED_API_KEY, FMP_API_KEY, SEC_EDGAR_CONTACT_*)
├── config/
│   ├── settings.py            # pydantic-settings; load_{factors,gemini,portfolio,risk,simulation}_config()
│   └── config.example.yaml    # non-secret params incl. factors/portfolio/risk/simulation/gemini sections
├── data/
│   ├── db.py                  # SQLite schema (14 tables + sync_state watermarks)
│   ├── universe.py             # S&P 500 from Wikipedia (503 tickers + CIK)
│   ├── backfill.py              # full historical backfill CLI script
│   ├── daily_sync.py             # incremental daily job (uses sync_state)
│   └── ingestion/
│       ├── yfinance_client.py    # OHLCV + free multi-period statements (DEFAULT fundamentals source)
│       ├── fmp_client.py          # fundamentals via FMP (optional, needs FMP_API_KEY)
│       ├── fundamentals_normalizer.py  # FMP/yfinance -> one canonical schema
│       ├── sec_edgar_client.py     # 10-K/10-Q metadata + fetch_filing_text() (specific-accession full text,
│       │                            # disk-cached), Form 4 XML parsing, 13F bulk dataset
│       ├── congressional/          # house_clerk.py (works), senate_efd.py (Akamai-blocked from dev network)
│       ├── fred_client.py         # PPI (PPIACO), DXY proxy (DTWEXBGS), BDI proxy (PCU483111483111)
│       ├── finra_short_interest.py # free, no key
│       ├── analyst_estimates.py    # yfinance-based, free
│       └── quiverquant_client.py    # paid alt-data, kept but not primary path
├── factors/                    # THE SCORING ENGINE
│   ├── engine.py                # ScoringEngine — the main entrypoint, run this
│   ├── data_loader.py             # SQL -> DataFrame loaders (prices, volume, sector map/ETFs, next-session
│   │                                # open, ADV, close-on-or-before — the last three added for paper trading)
│   ├── scoring.py                  # sector_neutral_zscore, composite_score (graceful degradation)
│   ├── crowding.py                  # Euler variance-decomposition crowding detector
│   ├── momentum.py, value.py, quality.py, growth.py,
│   │   estimate_revisions.py, insider_activity.py,
│   │   institutional_flow.py, congressional.py   # the 8 factors
│   └── macro_regime.py               # PPI/DXY/BDI trend — separate, NOT blended into composite
├── ai_analysis/                 # Gemini qualitative overlay — FULLY BUILT
│   ├── gemini_client.py            # google-genai wrapper: RateLimiter-throttled + reactive 429 backoff,
│   │                                 # complete() (plain text) and complete_structured() (Pydantic schema)
│   ├── rate_limiter.py              # sliding-window RPM/RPD throttle (config-driven, gemini-flash-latest
│   │                                 # free tier defaults: 15 RPM / 1500 RPD)
│   ├── cache.py                      # get_cached()/store_cache() against the shared ai_analysis_cache table
│   ├── filing_sections.py             # fetches+caches filing text, extracts Item N sections (TOC-vs-real-
│   │                                   # heading disambiguation via a content-length heuristic)
│   ├── filing_structure_analyzer.py    # analyzer 1: 10-K/10-Q structural anomalies
│   ├── risk_factor_analyzer.py          # analyzer 2: Item 1A changes between consecutive filings
│   ├── insider_transaction_analyzer.py   # analyzer 3: cluster-buy vs. routine option-exercise-sale
│   ├── transcript_sentiment_analyzer.py   # analyzer 4: earnings-call sentiment — takes transcript_text
│   │                                        # directly, no ingestion (no free full-transcript API exists)
│   ├── weekly_commentary.py               # analyzer 5: weekly trade-log/risk/performance commentary (api/routers/ai.py)
│   └── lp_letter.py                        # analyzer 6: LP-style letter over the same weekly data
├── portfolio/                   # FULLY BUILT
│   ├── construction.py             # construct_portfolio() — mode toggle (portfolio.mode: "mvo" |
│   │                                 # "conviction_tilt"), select_long_short_candidates, conviction_tilt_positions,
│   │                                 # apply_turnover_budget, apply_beta_neutralization
│   ├── optimization.py              # mean_variance_optimize — cvxpy; target-volatility HARD constraint,
│   │                                 # sector-neutrality band, beta-neutrality band, turnover budget,
│   │                                 # transaction-cost term in the objective
│   └── risk_models.py                # estimate_covariance_matrix (fixed-intensity diagonal shrinkage),
│                                       # estimate_beta (Cov/Var regression vs. a benchmark)
├── risk/                        # FULLY BUILT — independent, veto-capable (see CLAUDE.md)
│   ├── risk_management.py          # check_position_limits/check_sector_limits/check_beta_neutrality/
│   │                                 # check_turnover/historical_var — flag/report-only primitives
│   ├── circuit_breaker.py           # check_circuit_breaker (daily loss >2.5% or drawdown >8%) +
│   │                                 # apply_circuit_breaker (halt new entries, still allow de-risking)
│   ├── decomposition.py              # factor vs. specific risk, single-factor-per-sector model,
│   │                                 # flags if specific share isn't ~80% (±10%)
│   ├── correlation_monitor.py         # flags held-position pairs correlated >0.85
│   ├── stress_testing.py              # REAL historical replay (not invented shocks) against verified
│   │                                 # 2008/2020/2022 S&P peak-trough windows, sector-ETF fallback for
│   │                                 # tickers without that much price history
│   └── gate.py                         # evaluate_portfolio() — the ONLY veto authority in this codebase;
│                                         # everything else above is flag-only
├── simulation/
│   ├── paper_trading.py            # OLD -- PaperTradingEngine, weight-based rebalancing. Superseded by
│   │                                 # virtual_ledger.py for anything API-driven; untouched, unused by any route.
│   ├── execution.py                  # estimate_slippage_bps (linear-in-ADV-participation), simulate_fill_price
│   │                                   # -- also only used by the old engine now, not the virtual ledger.
│   └── virtual_ledger.py               # NEW -- the manual decide-and-track loop: deposit/get_balance (always
│                                         # DERIVED, never stored)/execute_candidate (BEGIN IMMEDIATE overspend
│                                         # guard)/close_position/run_end_of_day (upsert-safe)/
│                                         # circuit_breaker_status (built from investment P&L, not
│                                         # total_account_value)/reset
├── api/                          # FULLY BUILT — FastAPI backend
│   ├── main.py                     # FastAPI app + CORS (localhost:3000, GET+POST) + router registration + init_db()
│   ├── engine_service.py            # the MODEL's view: compute_pipeline() runs ScoringEngine only (no MVO, no
│   │                                  # risk gate -- those don't fit a manual candidate-review flow), feeds
│   │                                  # factors.py and candidates.py
│   ├── ledger_service.py             # the LEDGER's view: risk/performance analysis over the user's actual open
│   │                                  # positions (reuses risk/decomposition.py, correlation_monitor.py,
│   │                                  # stress_testing.py, risk_management.py directly -- not through risk/gate.py,
│   │                                  # which is proposed-vs-previous-weights shaped and doesn't fit a real-holdings
│   │                                  # monitor); feeds risk.py and performance.py
│   ├── cache.py                      # generic in-process TTLCache (15 min default) for engine_service's pipeline
│   ├── schemas.py                     # Pydantic response models, one per endpoint
│   └── routers/                        # account.py, candidates.py, positions.py (all NEW this session),
│                                         # factors.py, risk.py (reworked), performance.py (reworked), ai.py
├── dashboard/app.py             # OLD Streamlit skeleton — superseded by api/ + web/, safe to delete
├── web/                          # FULLY BUILT — Next.js 16 (App Router/TS/Tailwind v4) frontend, see src/app/
│   └── src/
│       ├── app/                    # page.tsx = Portfolio (route "/", the daily loop), factors/, risk/,
│       │                            # execution/ (now lists positions, not fills), ai/
│       ├── components/               # app-shell.tsx (nav), portfolio-dashboard.tsx (the Portfolio view's
│       │                              # client-side state owner), candidate-card.tsx (Execute + AI Analysis
│       │                              # expander), ai-analysis-panel.tsx, position-card.tsx (Close), deposit-form.tsx,
│       │                              # ai-insight-card.tsx, crowding-chart.tsx, ui/ (stat-tile, sparkline,
│       │                              # factor-bar, badge, icons)
│       └── lib/                      # api.ts (fetch wrappers, GET+POST), types.ts (hand-mirrors api/schemas.py —
│                                       # keep in sync by hand, no shared codegen), format.ts (currency/pct
│                                       # formatting + normalizeLlmText() for Gemini's occasional literal "\n" text)
└── tests/
    ├── test_smoke.py             # imports every module + basic sanity checks
    ├── test_factors.py            # synthetic-data unit tests for the scoring engine
    ├── test_ai_analysis.py         # rate limiter, cache, filing-section parsing, all 4 analyzers (mocked Gemini)
    ├── test_portfolio.py            # covariance/beta, MVO constraints, conviction-tilt, mode toggle
    ├── test_risk.py                  # circuit breaker, decomposition, correlation monitor, stress replay, gate
    ├── test_simulation.py             # OLD engine: slippage model, lot accounting, rebalance end-to-end
    ├── test_api.py                     # engine_service._num() casting
    └── test_virtual_ledger.py           # deposit/execute/close cycle, the overspend-race guard, EOD idempotency,
                                           # the circuit-breaker deposit-contamination fix — synthetic data with
                                           # load_close_on_or_before monkeypatched (see its fixture docstring for why)
```

## How to run the pipeline right now

The block below demonstrates the underlying engine's automated path
(scoring → MVO → risk gate → the OLD `PaperTradingEngine`) — it's still
valid code and nothing stops you calling it directly, but **the live app
(`api/` + `web/`) no longer uses this path**. The app's daily loop is
manual/candidate-driven (`simulation/virtual_ledger.py`) — see "How to run
the reporting layer" below for that.

```python
from factors.engine import ScoringEngine
from portfolio.construction import construct_portfolio
from risk.gate import evaluate_portfolio
from simulation.paper_trading import PaperTradingEngine
from factors.data_loader import load_prices_wide, load_sector_map, load_sector_etf_prices

tickers = [...]  # or omit for full S&P 500
scores = ScoringEngine(tickers=tickers).run()["composite_score"].dropna()
sector_map = load_sector_map(tickers)
prices = load_prices_wide(tickers + ["SPY"], start_date="2025-01-01")  # SPY = benchmark_ticker

weights = construct_portfolio(
    scores=scores, sector_map=sector_map, prices=prices, benchmark_ticker="SPY",
)  # mode from config.yaml's portfolio.mode, defaults to "mvo"

# ALWAYS route through the risk gate before acting on weights — it's the
# only thing with veto authority (see CLAUDE.md).
from portfolio.risk_models import estimate_beta
returns = prices[weights.index].pct_change().dropna(how="all")
beta = estimate_beta(returns, prices["SPY"].pct_change().dropna()).reindex(weights.index).fillna(0.0)
sector_etf_prices = load_sector_etf_prices(start_date="2025-01-01")

verdict = evaluate_portfolio(
    proposed_weights=weights, previous_weights=None, prices=prices,
    sector_etf_prices=sector_etf_prices, sector_map=sector_map, beta=beta,
    max_position_weight=0.03, max_sector_weight=0.20, beta_neutrality_tolerance=0.10,
)
# verdict.approved, verdict.weights (use THIS, not `weights`, downstream), verdict.warnings, verdict.checks

engine = PaperTradingEngine(run_id="daily", starting_cash=10_000_000, db_path=None)  # None = real research.db
result = engine.rebalance(verdict.weights, as_of_date="2026-07-17")  # decision date; fills at the next session's open
```

To populate the database first: `python -m data.backfill --tickers AAPL MSFT ...`
(or no `--tickers` for the full S&P 500 — slow). `python -m data.daily_sync` for
incremental updates after that.

## How to run the reporting layer right now

```bash
# terminal 1 — backend (needs GOOGLE_API_KEY in .env for the two /api/ai/* routes)
uvicorn api.main:app --reload --port 8000

# terminal 2 — frontend (reads web/.env.local for NEXT_PUBLIC_API_BASE_URL)
cd web && npm run dev
```
Then open `http://localhost:3000`. First thing to do on a clean checkout:
deposit virtual cash (`POST /api/account/deposit {"amount": 100000}` or
the Deposit Funds control in the UI) — everything starts at $0 by design.
The candidate universe is whatever's both an S&P 500 constituent and
present in `prices_daily` (currently the 33-ticker diversified set + SPY
— see `api/engine_service.py`'s `default_universe()`); `/api/candidates`
takes `?num_longs=&num_shorts=` to change how many are surfaced (default
15 each), not a ticker override.

## Known gaps / TODO (in priority order)

1. No `FMP_API_KEY` or `FRED_API_KEY` configured — fundamentals correctly
   fall back to yfinance's free statements (thoroughly tested), but
   FRED-dependent factors/macro regime return empty/`unknown` until a key
   is added.
2. `senate_efd.py` is built against the documented official flow but
   returns a `SenateAccessBlockedError` from the dev network (Akamai bot
   protection).
3. 13F holdings are ticker-matched by normalized company name, not exact
   CUSIP (no free CUSIP↔ticker mapping exists) — documented in
   `sec_edgar_client.py`.
4. No free full earnings-call-transcript API exists (Finnhub/API Ninjas
   gate it to paid plans, FMP markets it as a premium dataset) — by design,
   `TranscriptSentimentAnalyzer.analyze()` takes `transcript_text` directly
   rather than fetching it; the AI Analysis panel always reports this
   section unavailable, never fakes it.
5. **The universe is only 33 tickers + SPY** — everything in `api/`/`web/`
   was built and tested against that, not the full S&P 500 (which isn't
   fully backfilled). `ScoringEngine.run()` alone (no MVO anymore — see
   above) is fast even at 33 tickers; worth re-timing at 500 before
   assuming the 15-minute cache TTL in `api/cache.py` is still right.
6. **AI Analysis first-expand latency is bounded but not fast**: on the
   first click for a given ticker, `candidates.py` does live SEC EDGAR
   calls (filing list + up to `FORM4_FETCH_LIMIT=20` individual Form 4
   fetches, ~0.15s apart each per SEC's fair-access policy) plus 1-2
   Gemini calls — this session measured ~50s worst case for a
   heavily-filed ticker (COP). Every expand after that is cache-fast
   (<1s). If this needs to be snappier, the next lever is prefetching/
   warming the cache for the day's candidate list server-side rather than
   waiting for a user click.
7. The performance endpoint's `beta`/`alpha`/`factor_contribution` fields
   come back `null` until `account_daily_snapshots` has 60+ days of
   history (`portfolio/risk_models.py`'s `estimate_beta()` won't estimate
   off less than that, by design — see `api/ledger_service.py`'s
   `get_performance()` docstring). Expected to be `null` on any fresh
   account for a couple months, not a bug.
8. `dashboard/app.py` (the old Streamlit skeleton) and
   `simulation/paper_trading.py` (the old weight-based engine) are both
   dead code now that `api/`/`web/`/`virtual_ledger.py` exist — nobody's
   deleted them yet, kept around since nothing depends on removing them.

## Test status

95 tests passing (`python -m pytest tests/ -v`): 3 smoke, 9 factor-engine,
13 ai_analysis, 19 portfolio, 20 risk, 14 simulation (old engine), 2 api,
15 virtual_ledger (added this session) — plus a clean `web/` typecheck
(`npx tsc --noEmit`), lint (`npm run lint`), and production build
(`npm run build`), none of which show up in the Python count. All
synthetic-data unit tests plus live verification against real SEC/price/
Gemini data during each build (never committed to the repo, disk caches
under `data/raw/` are gitignored).

## Git history

```
9da484c Update CLAUDE.md and PROJECT_SUMMARY.md for the portfolio/risk/simulation build
de1e1fe Build AI analysis, portfolio construction, risk management, and paper trading layers
49a0427 Add PROJECT_SUMMARY.md as a bridge doc for new sessions
528ef4a Move CLAUDE.md into the project root and update conventions
1ae266e Build the quantitative scoring engine
0ecafff Build S&P 500 data infrastructure layer
6648edc Switch to Gemini and free-tier data sources per CLAUDE.md
7201b5b Initial project scaffold
```
