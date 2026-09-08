import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.models.base import ModelError
from app.models.factory import local_ollama_origin

router = APIRouter(prefix="/api/model")


class ModelStatus(BaseModel):
    provider: str
    model: str
    available: bool = False


@router.get("/status", response_model=ModelStatus)
async def model_status(request: Request) -> ModelStatus:
    """Check the local model catalogue only; no prompts or external API requests."""
    settings = request.app.state.settings
    status = ModelStatus(provider=settings.model_provider, model=settings.model_name)
    if settings.model_provider != "ollama":
        return status
    try:
        origin = local_ollama_origin(settings.model_base_url)
        async with httpx.AsyncClient(
            timeout=2.0, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.get(f"{origin}/api/tags")
            response.raise_for_status()
            data = response.json()
        entries = data.get("models", []) if isinstance(data, dict) else []
        name = settings.model_name
        expected_name = name if ":" in name else f"{name}:latest"
        status.available = isinstance(entries, list) and any(
            isinstance(entry, dict) and entry.get("name") == expected_name for entry in entries
        )
    except (httpx.HTTPError, ValueError, ModelError):
        pass
    return status
