"""Resolve credentials once, without persisting environment secrets or crossing services."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import SecretStr


def service_identity(provider: str, base_url: str) -> tuple:
    try:
        url = urlsplit(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            return ()
        return (
            provider,
            url.scheme,
            url.hostname.lower(),
            url.port if url.port is not None else (443 if url.scheme == "https" else 80),
            url.path.rstrip("/") + "/",
        )
    except ValueError:
        return ()


def usable(key: SecretStr) -> bool:
    return key.get_secret_value().strip().lower() not in {"", "ollama"}


@dataclass(frozen=True)
class Credential:
    key: SecretStr = SecretStr("")
    source: str = "none"
    location: str | None = None

    def public(self) -> dict:
        return {
            "api_key_set": usable(self.key),
            "api_key_source": self.source,
            "api_key_location": self.location,
        }


def resolve_credential(settings, config=None) -> Credential:
    provider = config.provider if config is not None else settings.model_provider
    base_url = config.base_url if config is not None else settings.model_base_url
    service = service_identity(provider, base_url)
    explicit = config.api_key if config is not None else settings._saved_model_api_key
    explicit_service = service if config is not None else settings._saved_model_service
    if service and service == explicit_service and usable(explicit):
        return Credential(explicit, "saved", "model_settings")
    if (
        service
        and service == settings._environment_model_service
        and usable(settings._environment_model_api_key)
    ):
        return Credential(
            settings._environment_model_api_key,
            "MODEL_API_KEY",
            settings._credential_sources["model_api_key"],
        )
    if service == ("openai", "https", "api.openai.com", 443, "/v1/") and usable(
        settings.openai_api_key
    ):
        return Credential(
            settings.openai_api_key,
            "OPENAI_API_KEY",
            settings._credential_sources["openai_api_key"],
        )
    return Credential(SecretStr("ollama"), "ollama") if provider == "ollama" else Credential()
