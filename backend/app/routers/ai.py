from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.auth import require_auth
from app.models import AIGenerateRequest, AIFixRequest, AIResponse
from app.services.connection import get_connection_string
from app.services import ai as ai_service

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post("/generate", response_model=AIResponse)
async def generate(
    body: AIGenerateRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, body.server_id, body.database)
    try:
        result = ai_service.generate_sql(conn_str, body.database, body.prompt, body.current_sql)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI request failed: {e}")
    return AIResponse(**result)


@router.post("/fix", response_model=AIResponse)
async def fix(
    body: AIFixRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    conn_str = await get_connection_string(db, body.server_id, body.database)
    try:
        result = ai_service.fix_sql(conn_str, body.database, body.sql, body.error)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI request failed: {e}")
    return AIResponse(**result)
