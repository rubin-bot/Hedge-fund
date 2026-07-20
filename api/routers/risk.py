from fastapi import APIRouter

from api import ledger_service
from api.schemas import RiskResponse

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("", response_model=RiskResponse)
def get_risk_report():
    """Analyzes the user's actual open positions (the virtual ledger),
    not the model's hypothetical target book -- see api/ledger_service.py.
    """
    return ledger_service.build_risk_response()
