# Long-Short Equity Research System

A quantitative long-short equity research platform that combines traditional
factor investing (value, momentum, quality, growth) with Gemini-based
qualitative analysis of SEC filings and earnings call transcripts, wrapped in
a portfolio construction, risk management, and paper-trading pipeline with a
Streamlit dashboard. Data sourcing defaults to free tiers wherever possible
(see `CLAUDE.md`).

## Architecture

Data flows through the pipeline in one direction:

```
data → factors → ai_analysis → portfolio → risk → simulation → dashboard
```

1. **`data/`** — Ingestion scripts pull raw prices, fundamentals, and
   alternative data into `data/raw/`, then write cleaned/derived tables to
   `data/processed/`. Prices and fundamentals come from `yfinance`
   (`yfinance_client.py`, free, no key), filing documents from SEC EDGAR
   (`sec_edgar_client.py`, free, no key), and macro series from `FRED`
   (`fred_client.py`, free tier). `QuiverQuant` (`quiverquant_client.py`)
   supplies alternative data (e.g. congressional/insider trading) — this is
   the one paid dependency, kept because there's no free equivalent for that
   signal.

2. **`factors/`** — The scoring engine. `definitions.py` computes individual
   factors (value, momentum, quality, growth) from processed data;
   `scoring.py` normalizes and combines them into a single composite score
   per ticker used to rank the investable universe.

3. **`ai_analysis/`** — Gemini-based qualitative overlay. `gemini_client.py`
   wraps the `google-genai` SDK (with retry/backoff for free-tier rate
   limits); `filing_analysis.py` and `transcript_analysis.py` prompt Gemini
   to extract risk factors, red flags, guidance changes, and management tone
   shifts from 10-K/10-Q filings (pulled via `sec_edgar_client.py`) and
   earnings call transcripts, producing a qualitative signal that
   complements the quantitative factor scores.

4. **`portfolio/`** — Turns ranked/scored tickers into a tradable portfolio.
   `construction.py` selects long and short candidates and applies simple
   sizing rules; `optimization.py` runs a risk-constrained mean-variance
   optimizer (via `cvxpy`) subject to gross/net exposure and position-size
   limits.

5. **`risk/`** — Independent risk oversight. `risk_management.py` checks
   position/sector exposure limits and computes basic risk metrics (e.g.
   historical VaR); `stress_testing.py` runs the portfolio through
   historical and hypothetical shock scenarios.

6. **`simulation/`** — `paper_trading.py` is a simulated execution engine
   that turns target portfolio weights into simulated fills (with slippage
   and commission assumptions), tracks cash and positions, and marks the
   book to market over time.

7. **`dashboard/`** — `app.py` is a Streamlit app that visualizes current
   holdings, factor exposures, risk/stress-test results, and paper-trading
   PnL, reading from the outputs of the stages above.

`config/` centralizes settings: `settings.py` (a `pydantic-settings`
`BaseSettings` model) loads API keys from `.env`, and `config.example.yaml`
holds non-secret run parameters (universe definition, rebalance frequency,
exposure/risk limits). `tests/` contains smoke tests that import every
package and a couple of unit tests for the scoring logic.

See `CLAUDE.md` for the project's working conventions (free-tier-first data
sourcing, Gemini-only for LLM calls, caching expensive calls, commenting
non-obvious financial calculations).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # then fill in GOOGLE_API_KEY, QUIVERQUANT_API_KEY, FRED_API_KEY
copy config\config.example.yaml config\config.yaml   # optional, for local overrides
```

## Running

```bash
python -m data.ingestion.run_ingestion --tickers AAPL MSFT GOOGL
python -m pytest tests/
streamlit run dashboard/app.py
```

## Status

This is a scaffold: module interfaces and file layout are in place, but the
factor/optimization math and the paper-trading fill logic are stubbed with
`TODO`s / `NotImplementedError` pending implementation.
