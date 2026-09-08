from urllib.parse import urlsplit

from app.config import Settings
from app.models.base import ModelClient, ModelError
from app.models.openai_compatible import OpenAICompatibleClient


def local_ollama_origin(base_url: str) -> str:
    """Only the host's default Ollama endpoint is permitted for local providers."""
    try:
        url = urlsplit(base_url)
        valid = (
            url.scheme == "http"
            and url.hostname in {"127.0.0.1", "localhost", "::1"}
            and url.port == 11434
            and url.path.rstrip("/") == "/v1"
            and not (url.username or url.password or url.query or url.fragment)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ModelError("Ollama must use a localhost HTTP endpoint on port 11434 with /v1/")
    return f"{url.scheme}://{url.netloc}"


def create_model(settings: Settings) -> ModelClient:
    if settings.model_provider == "ollama":
        local_ollama_origin(settings.model_base_url)
    elif settings.model_provider == "openai":
        if not settings.model_api_key.get_secret_value():
            raise ModelError("An API key is required for the external provider")
    else:
        raise ModelError(f"Unknown model provider: {settings.model_provider}")
    return OpenAICompatibleClient(settings)
