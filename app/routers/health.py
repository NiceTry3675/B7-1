from fastapi import APIRouter

from app import schemas as s

router = APIRouter(tags=["health"])


@router.get("/health", response_model=s.Health)
async def health():
    return {"status": "ok"}
