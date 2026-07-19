# Long-short equity research system

Python 3.11+ quantitative long-short equity system: data ingestion, a
multi-factor scoring engine, AI filing/transcript analysis, portfolio
construction, risk management, paper-trading simulation, and a dashboard.

## Data source policy
- Prefer free API tiers and free data sources over paid ones.
- Before using any paid tier or endpoint, tell me first: explain what the
  free tier can't do and why the paid one is needed. Don't silently build
  against a paid plan.
- Before scraping or integrating any external source (especially "free
  community dataset" type sources), verify it's actually live and current
  right now — check the site resolves, check the GitHub repo's last commit
  date, don't assume a source is still maintained just because it's
  well-known. Free trackers die silently and stay indexed/recommended for
  years after going dark (e.g. House/Senate Stock Watcher — both looked
  legitimate, both had been dead for years).
- Keep all API keys in `.env`, never hardcoded, never in `requirements.txt`
  or any other tracked file. Never commit `.env`.

## Data sources currently wired up (all free unless noted)
- **Prices**: yfinance (OHLCV + free multi-period financial statements —
  the fundamentals fallback when no FMP key is set)
- **Fundamentals**: FMP if `FMP_API_KEY` is set (paid), else yfinance's
  free statements — both normalized to one canonical schema in
  `data/ingestion/fundamentals_normalizer.py` so the factor layer doesn't
  care which source populated a row
- **SEC EDGAR**: 10-K/10-Q filings, Form 4 insider transactions (parsed
  from raw XML), Form 13F institutional holdings (parsed from SEC's bulk
  quarterly structured data sets) — all via `data.sec.gov`/`www.sec.gov`
  with a compliant User-Agent (see `SEC_EDGAR_CONTACT_NAME`/`_EMAIL` in
  `.env`)
- **Congressional trading**: scraped directly from the official House
  Clerk (PDF Periodic Transaction Reports) and Senate eFD sites — not any
  third-party tracker. Senate eFD is blocked by Akamai bot protection from
  some networks/IPs (confirmed during dev); it fails loudly with a
  dedicated `SenateAccessBlockedError` rather than silently returning
  nothing, so retry from a different network before assuming it's broken.
- **FRED**: PPI (`PPIACO`), a DXY proxy (`DTWEXBGS`, Fed trade-weighted
  dollar index — FRED has no direct DXY), a Baltic Dry Index proxy
  (`PCU483111483111`, deep-sea freight PPI — FRED has no direct BDI either)
- **Short interest**: FINRA's free consolidated short interest API
  (biweekly, ~2-week reporting lag)
- **Analyst estimates**: yfinance (free, unofficial — price targets, EPS/
  revenue estimates, recommendation counts). Comprehensive structured
  estimates are an FMP paid-tier feature.
- **QuiverQuant**: kept as a paid alt-data client for congressional/
  insider data if a key is ever added, but not the primary path for either
  anymore now that the free scrapers above exist.

## LLM provider
- Use Google's Gemini API for all LLM calls (filing analysis, commentary).
  Use the google-genai Python SDK and a GOOGLE_API_KEY from .env.
  Do not use the Anthropic SDK.
- Respect free-tier rate limits: build rate-limiting/backoff into any code
  that makes LLM calls in a batch, so a full backfill doesn't blow past
  per-minute or per-day caps (see `ai_analysis/gemini_client.py`).
- Pin model names to alias-style IDs (e.g. `gemini-flash-latest`) rather
  than a specific dated version — dated model IDs get deprecated for new
  API keys without much warning.

## Scraper conventions (congressional trading, and any future scraper)
- Isolate each scraper in its own module (`data/ingestion/congressional/`).
- Fail loudly on structural changes: raise a specific exception
  (`SourceStructureError` / `SenateAccessBlockedError`) rather than
  silently returning empty data, and distinguish "source blocked us"
  from "source changed its layout" — they need different fixes.
- Cache fetched documents to disk (`data/raw/...`) so re-runs don't
  re-download unchanged filings.
- When parsing something messy (PDF tables, HTML with inconsistent
  structure), prefer a hybrid strategy with a documented fallback path
  over a single brittle regex — see `house_clerk.py`'s PTR parser.

## Factor engine conventions (`factors/`)
- Every stock-level factor is z-scored **within GICS sector**
  (`sector_neutral_zscore`) before being blended — comparing raw P/E or
  momentum across sectors just re-derives the sector, not mispricing.
- Any factor that can legitimately have sparse/missing data (needs
  historical snapshots that don't exist yet, depends on a scraped source
  that can go quiet, etc.) is an **optional factor**: the composite scorer
  drops it for a period if universe-wide coverage is too low and
  renormalizes remaining weights, and separately renormalizes weights
  per-ticker when just one entity is missing one factor. Required factors
  (built from prices/fundamentals, which should always be populated once
  backfilled) raise loudly instead of degrading — a coverage gap there is
  a real pipeline bug, not expected sparsity.
- Macro-level series (PPI, DXY proxy, BDI proxy) are never blended into
  the stock-level composite — they're the same for every ticker, so
  z-scoring them would just zero them out. They're a separate regime
  indicator for the portfolio/risk layer to condition on.
- Any diagnostic that decomposes variance/contributions (e.g. the factor
  crowding detector) must use the *same* factor set the composite scorer
  actually used post-dropout, not the raw pre-dropout set — feeding it a
  factor the scorer already dropped for thin coverage silently corrupts
  the complete-case sample.

## Portfolio, risk, and simulation conventions (`portfolio/`, `risk/`, `simulation/`)
- `portfolio/construction.py`'s `construct_portfolio()` is the single
  entry point for turning composite scores into target weights, picked
  between two modes via `portfolio.mode` in config (`"mvo"` — cvxpy
  mean-variance optimizer, `portfolio/optimization.py` — or
  `"conviction_tilt"` — a simpler score-weighted baseline). Both modes
  select the same candidate set (`select_long_short_candidates`) so the
  difference under test is the weighting scheme, not the universe — that's
  what makes conviction-tilt a fair baseline to compare MVO against, not a
  different strategy entirely.
- `risk/gate.py`'s `evaluate_portfolio()` is the **only** thing in this
  codebase with veto authority — it can return weights that differ from
  what `construct_portfolio()` proposed. Everything else in `risk/`
  (`risk_management.py`'s position/sector/beta/turnover checks,
  `correlation_monitor.py`, `decomposition.py`) is flag/report-only and
  never changes the output; only `circuit_breaker.py`'s
  `check_circuit_breaker`/`apply_circuit_breaker` gate. Don't hand
  `construct_portfolio()`'s output straight to `simulation.paper_trading`
  or the dashboard without routing it through `evaluate_portfolio()` first
  — that's the whole point of it being independent.
- Circuit-breaker logic (the "halt new position entry, still allow
  de-risking" rule) lives in exactly one place, `risk/circuit_breaker.py`,
  and is reused verbatim everywhere it's needed (`risk/gate.py`,
  `simulation/paper_trading.py`) rather than reimplemented — if the halt
  rule ever needs to change, it only needs to change there.
- Covariance/beta estimation goes through `portfolio/risk_models.py`
  (`estimate_covariance_matrix`'s fixed-intensity diagonal shrinkage,
  `estimate_beta`'s Cov/Var regression) wherever it's needed (MVO,
  `risk/decomposition.py`'s factor loadings) — one estimator, not
  reimplemented per caller.
- Every module here follows the same config precedence as `factors/engine.py`:
  explicit constructor/function arg > `config/config.yaml`'s section (via
  `load_portfolio_config()`/`load_risk_config()`/`load_simulation_config()`
  in `config/settings.py`) > hardcoded default.
- When verifying against real data in a dev session where a backfill or
  other writer might already hold `data/processed/research.db`'s lock,
  don't write to it directly — copy it via SQLite's online backup API
  (`sqlite3.connect(src).backup(dest_conn)`, safe against a live WAL-mode
  DB unlike a raw file copy) into an isolated temp DB and verify there.

## Conventions
- Cache expensive API and LLM calls (especially Gemini filing analysis) so
  nothing is fetched or analyzed twice.
- Write tests with pytest as features are built, not only at the end —
  validate new ingestion clients/factors against real live data during
  development (small-scale smoke tests), not just mocks; add permanent
  synthetic-data unit tests to the suite once the real-data path is proven.
- Explain non-obvious financial calculations (Piotroski F-Score, Altman
  Z-Score, Euler variance decomposition, MVO constraints, etc.) in
  comments — I want to understand and be able to defend every part.
