from __future__ import annotations

from fastapi import APIRouter

from backend.api.schemas import HealthResponse

router = APIRouter(
  prefix="/api",
  tags=["health"],
)


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
  return HealthResponse()