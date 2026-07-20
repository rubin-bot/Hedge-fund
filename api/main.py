"""FastAPI entrypoint. Run with:

    uvicorn api.main:app --reload --port 8000

Every route here wraps existing factors/, risk/, simulation/, and
ai_analysis/ modules (see api/engine_service.py and api/ledger_service.py)
-- nothing in those packages is reimplemented here. This is a virtual
paper-trading research system: every endpoint that "executes" a trade only
ever touches the local SQLite virtual cash ledger (simulation/virtual_ledger.py)
-- no endpoint places a real order or moves real money.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import account, ai, candidates, factors, performance, positions, risk
from data.db import init_db

app = FastAPI(
    title="Long-Short Equity Research Reporting API",
    description="Virtual paper-trading daily decision-and-track loop over the research engine. All figures are SIMULATED.",
    version="0.2.0",
)

# Local Next.js dev server origins. Tighten this once the frontend has a
# deployed origin -- wildcarding is fine for a local research tool, not for
# anything exposed beyond localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _ensure_schema() -> None:
    # Every table (including the virtual-ledger tables) is created lazily
    # via CREATE TABLE IF NOT EXISTS -- calling this here means every
    # router can query them safely on a fresh checkout, instead of each
    # one having to catch "no such table".
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(factors.router)
app.include_router(risk.router)
app.include_router(performance.router)
app.include_router(ai.router)
app.include_router(account.router)
app.include_router(candidates.router)
app.include_router(positions.router)
