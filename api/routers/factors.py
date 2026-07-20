from fastapi import APIRouter, Query

from api import engine_service
from api.schemas import FactorResponse

router = APIRouter(prefix="/api/factors", tags=["factors"])


@router.get("", response_model=FactorResponse)
def get_factor_breakdown(
    tickers: list[str] | None = Query(default=None, description="Override the default backfilled universe."),
    refresh: bool = Query(default=False, description="Bypass the pipeline cache and recompute."),
):
    return engine_service.build_factor_response(tickers=tickers, refresh=refresh)
