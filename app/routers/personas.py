from fastapi import APIRouter

from app import schemas as s
from app.dependencies import DatabaseDep, UserDep, db_call
from app.errors import COMMON_ERRORS, error_docs

router = APIRouter(prefix="/personas", tags=["personas"])


@router.get("", response_model=s.Personas, responses=error_docs(*COMMON_ERRORS))
async def personas(user: UserDep, db: DatabaseDep):
    return {"items": await db_call(db.personas)}
