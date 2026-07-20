from fastapi import APIRouter

from api import ledger_service
from api.schemas import PerformanceResponse

router = APIRouter(prefix="/api/performance", tags=["performance"])


@router.get("", response_model=PerformanceResponse)
def get_performance():
    """Reads account_daily_snapshots/positions (the virtual ledger) --
    see api/ledger_service.py.
    """
    return ledger_service.get_performance()
