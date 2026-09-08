from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix="/api")


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Check SQLite and report the configured provider without contacting a model."""
    try:
        await request.app.state.database.check_connection()
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "database": "error",
                "model_provider": request.app.state.settings.model_provider,
            },
        )
    return JSONResponse(
        content={
            "status": "ok",
            "database": "ok",
            "model_provider": request.app.state.settings.model_provider,
        }
    )
