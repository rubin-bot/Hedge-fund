# Project Summary — read this first in any new session

Bridge document for picking up this project cold. For behavioral
conventions/policy (data source rules, scraper conventions, factor/portfolio/
risk conventions), read `CLAUDE.md` too — that's the authoritative, actively
maintained policy file; this document is a point-in-time state snapshot
(as of 2026-07-19) and can go stale, so verify against the actual code
before relying on specifics here.

## What this is

A Python quantitative long-short equity research system: data ingestion →
factor scoring → AI filing/transcript analysis → portfolio construction →
risk management → paper-trading simulation → dashboard. Free-tier-first
data sourcing, Gemini for all LLM calls (see `CLAUDE.md`).

**Everything through paper trading is now built and tested (78 passing
tests).** The dashboard is the one remaining unbuilt piece.

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
│   └── transcript_sentiment_analyzer.py   # analyzer 4: earnings-call sentiment — takes transcript_text
│                                            # directly, no ingestion (no free full-transcript API exists)
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
├── simulation/                  # FULLY BUILT
│   ├── paper_trading.py            # PaperTradingEngine — rebalance() sizes off decision-date close, fills
│   │                                 # at next-session open, average-cost lot P&L (symmetric long/short,
│   │                                 # flip-splitting), SQLite trade log + snapshots, wired to
│   │                                 # risk/circuit_breaker.py via its own persisted equity history
│   └── execution.py                  # estimate_slippage_bps (linear-in-ADV-participation), simulate_fill_price
├── dashboard/app.py             # Streamlit skeleton, NOT wired to real data — NEXT BUILD TARGET
└── tests/
    ├── test_smoke.py             # imports every module + basic sanity checks
    ├── test_factors.py            # synthetic-data unit tests for the scoring engine
    ├── test_ai_analysis.py         # rate limiter, cache, filing-section parsing, all 4 analyzers (mocked Gemini)
    ├── test_portfolio.py            # covariance/beta, MVO constraints, conviction-tilt, mode toggle
    ├── test_risk.py                  # circuit breaker, decomposition, correlation monitor, stress replay, gate
    └── test_simulation.py             # slippage model, lot accounting (every branch), rebalance end-to-end,
                                         # the circuit-breaker-halts-trading test
```

## How to run the pipeline right now

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

## Known gaps / TODO (in priority order)

1. **`dashboard/` is the only unbuilt piece.** `app.py` is a Streamlit
   skeleton not wired to real data. It should read from
   `construct_portfolio()` → `risk.gate.evaluate_portfolio()` →
   `PaperTradingEngine`'s SQLite tables (`paper_trades`,
   `paper_portfolio_snapshots`) — don't have it call `construct_portfolio()`
   directly and skip the risk gate.
2. **A background backfill process from an earlier session appears
   stuck.** PID 24656 (`python.exe`, started 2026-07-19 19:17, command
   backfills the 33-ticker diversified universe + Form4/13F/congressional/
   short-interest/analyst-estimates) has accumulated only ~0.015s of CPU
   time despite running for hours — `sec_filings`, `sec_form4_transactions`,
   `sec_13f_holdings`, `congressional_trades`, and `fred_series` are all
   still empty in the live DB (confirmed via direct query) while
   `short_interest`/`analyst_estimates` partially landed. This smells like
   a blocking network call with no timeout — Senate eFD's known Akamai
   block (see `CLAUDE.md`) is the top suspect. **Check whether that process
   is still alive before starting new backfill work**, and consider it safe
   to kill (`taskkill /PID 24656 /F` or your process manager of choice) if
   confirmed stuck — nothing in this repo depends on it finishing.
   Consider adding a `timeout=` to whichever `requests` call is hanging so
   this can't recur silently.
3. **SPY (the benchmark ticker) is not in the live `research.db`.** Every
   MVO/beta-neutral/stress-test verification this session was run against
   an isolated temp-DB copy (via SQLite's online backup API, to avoid
   fighting the stuck process above for the write lock) with SPY backfilled
   *there* — the real DB still lacks it. Run
   `python -c "from data.backfill import backfill_prices; backfill_prices(['SPY'], years=2)"`
   once the DB is free before relying on beta-neutral MVO or stress-test
   replay against real (non-test) data.
4. **New tables (`ai_analysis_cache`, `paper_trades`,
   `paper_portfolio_snapshots`) don't exist in the live DB yet** — they're
   created lazily via `CREATE TABLE IF NOT EXISTS` the first time
   `ai_analysis.cache`/`PaperTradingEngine` actually run against it; only
   exercised against isolated temp DBs so far this session.
5. No `FMP_API_KEY` or `FRED_API_KEY` configured — fundamentals correctly
   fall back to yfinance's free statements (thoroughly tested), but
   FRED-dependent factors/macro regime return empty/`unknown` until a key
   is added.
6. `senate_efd.py` is built against the documented official flow but
   returns a `SenateAccessBlockedError` from the dev network (Akamai bot
   protection) — likely the cause of gap #2 above if `backfill_congressional`
   is where that stuck process is actually blocked.
7. 13F holdings are ticker-matched by normalized company name, not exact
   CUSIP (no free CUSIP↔ticker mapping exists) — documented in
   `sec_edgar_client.py`.
8. No free full earnings-call-transcript API exists (Finnhub/API Ninjas
   gate it to paid plans, FMP markets it as a premium dataset) — by design,
   `TranscriptSentimentAnalyzer.analyze()` takes `transcript_text` directly
   rather than fetching it; sourcing transcripts is left to the caller.

## Test status

78 tests passing as of commit `de1e1fe` (`python -m pytest tests/ -v`): 3
smoke, 9 factor-engine, 13 ai_analysis, 19 portfolio, 20 risk, 14 simulation.
All synthetic-data unit tests plus live verification against real
SEC/price/Gemini data during each build (never committed to the repo, disk
caches under `data/raw/` are gitignored).

## Git history

```
de1e1fe Build AI analysis, portfolio construction, risk management, and paper trading layers
49a0427 Add PROJECT_SUMMARY.md as a bridge doc for new sessions
528ef4a Move CLAUDE.md into the project root and update conventions
1ae266e Build the quantitative scoring engine
0ecafff Build S&P 500 data infrastructure layer
6648edc Switch to Gemini and free-tier data sources per CLAUDE.md
7201b5b Initial project scaffold
```
