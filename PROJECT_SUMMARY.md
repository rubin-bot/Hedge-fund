# Project Summary — read this first in any new session

Bridge document for picking up this project cold. For behavioral
conventions/policy (data source rules, scraper conventions, factor
conventions), read `CLAUDE.md` too — that's the authoritative, actively
maintained policy file; this document is a point-in-time state snapshot
(as of 2026-07-19) and can go stale, so verify against the actual code
before relying on specifics here.

## What this is

A Python quantitative long-short equity research system: data ingestion →
factor scoring → AI filing/transcript analysis → portfolio construction →
risk management → paper-trading simulation → dashboard. Free-tier-first
data sourcing, Gemini for all LLM calls (see `CLAUDE.md`).

## Location

Git repo root: `C:\Users\RUBIN\Desktop\Projects_Cursor\Cludi\Hedge fund\`.
The sibling folder `Cludi\Test1\` is a **separate, unrelated** snake-game
project — never touch it, never assume its files are related.

## Directory map (only non-empty/meaningful files listed)

```
Hedge fund/
├── CLAUDE.md                 # policy: data sources, scraper/factor conventions — READ THIS
├── README.md                 # architecture explanation + setup instructions
├── PROJECT_SUMMARY.md         # this file
├── .env / .env.example        # API keys (GOOGLE_API_KEY, QUIVERQUANT_API_KEY,
│                               # FRED_API_KEY, FMP_API_KEY, SEC_EDGAR_CONTACT_*)
├── config/
│   ├── settings.py            # pydantic-settings; also load_factors_config()
│   └── config.example.yaml    # non-secret params incl. `factors:` weights section
├── ai_analysis/
│   ├── gemini_client.py       # google-genai wrapper, model=gemini-flash-latest, retry/backoff
│   ├── filing_analysis.py
│   └── transcript_analysis.py
├── data/
│   ├── db.py                  # SQLite schema (11 tables + sync_state watermarks)
│   ├── universe.py             # S&P 500 from Wikipedia (503 tickers + CIK)
│   ├── backfill.py              # full historical backfill CLI script
│   ├── daily_sync.py             # incremental daily job (uses sync_state)
│   └── ingestion/
│       ├── yfinance_client.py    # OHLCV + free multi-period statements (DEFAULT fundamentals source)
│       ├── fmp_client.py          # fundamentals via FMP (optional, needs FMP_API_KEY)
│       ├── fundamentals_normalizer.py  # FMP/yfinance -> one canonical schema
│       ├── sec_edgar_client.py     # 10-K/10-Q, Form 4 XML parsing, 13F bulk dataset
│       ├── congressional/
│       │   ├── house_clerk.py      # scrapes official House Clerk PTR PDFs — WORKS, tested on real data
│       │   ├── senate_efd.py        # scrapes official Senate eFD — blocked by Akamai from dev network
│       │   └── base.py               # SourceStructureError, trade_id hashing, disk cache path helper
│       ├── fred_client.py         # PPI (PPIACO), DXY proxy (DTWEXBGS), BDI proxy (PCU483111483111)
│       ├── finra_short_interest.py # free, no key
│       ├── analyst_estimates.py    # yfinance-based, free
│       └── quiverquant_client.py    # paid alt-data, kept but not primary path
├── factors/                    # THE SCORING ENGINE
│   ├── engine.py                # ScoringEngine — the main entrypoint, run this
│   ├── data_loader.py             # SQL -> DataFrame loaders
│   ├── scoring.py                  # sector_neutral_zscore, composite_score (graceful degradation)
│   ├── crowding.py                  # Euler variance-decomposition crowding detector
│   ├── momentum.py, value.py, quality.py, growth.py,
│   │   estimate_revisions.py, insider_activity.py,
│   │   institutional_flow.py, congressional.py   # the 8 factors
│   └── macro_regime.py               # PPI/DXY/BDI trend — separate, NOT blended into composite
├── portfolio/                  # STUBS ONLY — construction.py, optimization.py not implemented
├── risk/                       # STUBS ONLY — risk_management.py, stress_testing.py not implemented
├── simulation/                 # STUB ONLY — paper_trading.py not implemented
├── dashboard/app.py             # Streamlit skeleton, not wired to real data
└── tests/
    ├── test_smoke.py             # imports everything + basic sanity checks
    └── test_factors.py            # synthetic-data unit tests for the scoring engine
```

## How to run the engine right now

```python
from factors.engine import ScoringEngine

engine = ScoringEngine(tickers=["AAPL", "MSFT", ...])  # or omit for full S&P 500
result = engine.run()
result["composite_score"]   # raw weighted z-score composite, ticker -> score
result["rank"]               # percentile rank (0-1) version, for long/short selection
result["diagnostics"]         # which optional factors got dropped this run + coverage
result["crowding"]             # factor crowding detector output
result["macro_regime"]          # PPI/DXY-proxy/BDI-proxy trend, separate from stock scoring
```

To populate the database first: `python -m data.backfill --tickers AAPL MSFT ...`
(or no `--tickers` for the full S&P 500 — slow). `python -m data.daily_sync` for
incremental updates after that.

## Known gaps / TODO (in priority order in most recent conversation)

1. **`portfolio/`, `risk/`, `simulation/`, `dashboard/` are unbuilt** — next
   logical build targets. `factors/engine.py`'s output (composite score +
   rank) is what portfolio construction should consume.
2. No `FMP_API_KEY` or `FRED_API_KEY` configured — fundamentals correctly
   fall back to yfinance's free statements (this works fine and is
   thoroughly tested), but FRED-dependent factors and the macro regime
   indicator return empty/`unknown` until a FRED key is added.
3. `senate_efd.py` is built against the documented official flow but
   returns a `SenateAccessBlockedError` from the dev network (Akamai bot
   protection, confirmed via direct testing) — untested end-to-end for
   real; test it from wherever the daily job actually runs.
4. 13F holdings are ticker-matched by normalized company name, not exact
   CUSIP (no free CUSIP↔ticker mapping exists) — a small mismatch rate is
   expected and documented in `sec_edgar_client.py`.
5. A 33-ticker diversified backfill (all 11 GICS sectors: HON, UPS, CAT,
   JNJ, UNH, PFE, AAPL, MSFT, NVDA, NEE, DUK, SO, JPM, BAC, GS, LIN, SHW,
   FCX, AMZN, HD, MCD, PLD, AMT, EQIX, GOOGL, META, DIS, PG, KO, WMT, XOM,
   CVX, COP, plus sector ETFs XLK/XLF/XLE/XLV/XLI/XLY/XLP/XLU/XLB/XLC/XLRE)
   was run to validate the scoring engine end-to-end. Prices/fundamentals/
   analyst-estimates/short-interest all landed; Form4/13F/congressional
   were still running in the background when the session ended (SEC/House
   Clerk rate limits make these genuinely slow across many mega-caps) — may
   or may not have finished; check row counts in `sec_form4_transactions`,
   `sec_13f_holdings`, `congressional_trades` before assuming they're empty.

## Test status

All tests passing as of commit `528ef4a`: `python -m pytest tests/ -v`
(12 tests: 3 smoke, 9 factor-engine synthetic-data unit tests).

## Git history

```
528ef4a Move CLAUDE.md into the project root and update conventions
1ae266e Build the quantitative scoring engine
0ecafff Build S&P 500 data infrastructure layer
6648edc Switch to Gemini and free-tier data sources per CLAUDE.md
7201b5b Initial project scaffold
```
