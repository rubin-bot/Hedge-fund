# Long-Short Equity Research System

A quantitative long-short equity research platform that combines traditional
factor investing (value, momentum, quality, growth) with Claude-based
qualitative analysis of SEC filings and earnings call transcripts, wrapped in
a portfolio construction, risk management, and paper-trading pipeline with a
Streamlit dashboard.

## Architecture

Data flows through the pipeline in one direction:

```
data → factors → ai_analysis → portfolio → risk → simulation → dashboard
```

1. **`data/`** — Ingestion scripts pull raw prices, fundamentals, and
   alternative data into `data/raw/`, then write cleaned/derived tables to
   `data/processed/`. Market data comes from either FMP or Polygon, selected
   at runtime via the `DATA_PROVIDER` setting (`config/settings.py`), so
   either provider's API key works without code changes. `QuiverQuant`
   supplies alternative data (e.g. congressional/insider trading) and `FRED`
   supplies macroeconomic series.

2. **`factors/`** — The scoring engine. `definitions.py` computes individual
   factors (value, momentum, quality, growth) from processed data;
   `scoring.py` normalizes and combines them into a single composite score
   per ticker used to rank the investable universe.

3. **`ai_analysis/`** — Claude-based qualitative overlay. `claude_client.py`
   wraps the Anthropic SDK; `filing_analysis.py` and `transcript_analysis.py`
   prompt Claude to extract risk factors, red flags, guidance changes, and
   management tone shifts from 10-K/10-Q filings and earnings call
   transcripts, producing a qualitative signal that complements the
   quantitative factor scores.

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
`BaseSettings` model) loads API keys and the `DATA_PROVIDER` toggle from
`.env`, and `config.example.yaml` holds non-secret run parameters (universe
definition, rebalance frequency, exposure/risk limits). `tests/` contains
smoke tests that import every package and a couple of unit tests for the
scoring logic.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # then fill in your API keys
copy config\config.example.yaml config\config.yaml   # optional, for local overrides
```

Set `DATA_PROVIDER` in `.env` to `fmp` or `polygon` depending on which
market data key you have configured.

## Running

```bash
python -m data.ingestion.run_ingestion --tickers AAPL MSFT GOOGL
python -m pytest tests/
streamlit run dashboard/app.py
```

## Status

This is a scaffold: module interfaces and file layout are in place, but
provider API calls, the factor/optimization math, and the paper-trading
fill logic are stubbed with `TODO`s / `NotImplementedError` pending
implementation.
