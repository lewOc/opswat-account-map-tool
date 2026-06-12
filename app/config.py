"""Server-side configuration for the v2 app.

The v2 code keeps all provider keys on the server. Frontend requests should
select a provider, never submit raw API keys.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class AppEnv(str, Enum):
    dev = "dev"
    prod = "prod"


class AuthProvider(str, Enum):
    local = "local"
    oidc = "oidc"


class Settings(BaseModel):
    """Typed settings loaded from environment variables.

    This avoids v1's scattered `os.environ` lookups and hard-coded local paths.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    app_env: AppEnv = AppEnv.dev
    secret_key: Optional[SecretStr] = None
    database_url: str = "sqlite:///data/app.db"
    auth_provider: AuthProvider = AuthProvider.local

    anthropic_api_key: Optional[SecretStr] = None
    anthropic_model: str = "claude-opus-4-8"
    anthropic_fast_model: str = "claude-sonnet-4-6"
    anthropic_web_search_tool: str = "web_search_20250305"

    openai_api_key: Optional[SecretStr] = None
    openai_model: str = "gpt-5.5"
    openai_reasoning_effort: str = "medium"

    pinecone_api_key: Optional[SecretStr] = None
    customer_story_index: str = "opswat-docs"
    customer_story_namespace: str = "customer_stories"
    customer_story_embed_model: str = "text-embedding-3-large"
    customer_story_rag_disabled: bool = False

    enable_gpt_image: bool = False
    openai_image_model: str = "gpt-image-2"

    workers: int = Field(default=2, ge=1)
    narrative_concurrency: int = Field(default=3, ge=1)
    model_timeout_s: int = Field(default=600, ge=30)
    artifact_dir: Path = Path("var/artifacts")

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if environ is None else environ

        def get(name: str, default: Optional[str] = None) -> Optional[str]:
            value = env.get(name)
            if value is None or value == "":
                return default
            return value

        def get_bool(name: str, default: bool = False) -> bool:
            value = get(name)
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "on"}

        def get_int(name: str, default: int) -> int:
            value = get(name)
            return default if value is None else int(value)

        return cls(
            app_env=AppEnv(get("APP_ENV", AppEnv.dev.value)),
            secret_key=get("SECRET_KEY"),
            database_url=get("DATABASE_URL", "sqlite:///data/app.db") or "sqlite:///data/app.db",
            auth_provider=AuthProvider(get("AUTH_PROVIDER", AuthProvider.local.value)),
            anthropic_api_key=get("ANTHROPIC_API_KEY"),
            anthropic_model=get("ANTHROPIC_MODEL", "claude-opus-4-8") or "claude-opus-4-8",
            anthropic_fast_model=get("ANTHROPIC_FAST_MODEL", "claude-sonnet-4-6") or "claude-sonnet-4-6",
            anthropic_web_search_tool=get("ANTHROPIC_WEB_SEARCH_TOOL", "web_search_20250305") or "web_search_20250305",
            openai_api_key=get("OPENAI_API_KEY"),
            openai_model=get("OPENAI_MODEL", "gpt-5.5") or "gpt-5.5",
            openai_reasoning_effort=get("OPENAI_REASONING_EFFORT", "medium") or "medium",
            pinecone_api_key=get("PINECONE_API_KEY"),
            customer_story_index=get("CUSTOMER_STORY_INDEX", "opswat-docs") or "opswat-docs",
            customer_story_namespace=get("CUSTOMER_STORY_NAMESPACE", "customer_stories") or "customer_stories",
            customer_story_embed_model=get("CUSTOMER_STORY_EMBED_MODEL", "text-embedding-3-large")
            or "text-embedding-3-large",
            customer_story_rag_disabled=get_bool("CUSTOMER_STORY_RAG_DISABLED", False),
            enable_gpt_image=get_bool("ENABLE_GPT_IMAGE", False),
            openai_image_model=get("OPENAI_IMAGE_MODEL", "gpt-image-2") or "gpt-image-2",
            workers=get_int("WORKERS", 2),
            narrative_concurrency=get_int("NARRATIVE_CONCURRENCY", 3),
            model_timeout_s=get_int("MODEL_TIMEOUT_S", 600),
            artifact_dir=Path(get("ARTIFACT_DIR", "var/artifacts") or "var/artifacts"),
        )

    @field_validator("openai_reasoning_effort")
    @classmethod
    def validate_openai_reasoning_effort(cls, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("OPENAI_REASONING_EFFORT must be one of: low, medium, high")
        return value

    @model_validator(mode="after")
    def validate_prod_secret(self) -> "Settings":
        if self.app_env == AppEnv.prod and self.secret_key is None:
            raise ValueError("SECRET_KEY is required when APP_ENV=prod")
        return self

    def provider_api_key(self, provider: str) -> Optional[str]:
        if provider == "anthropic" and self.anthropic_api_key is not None:
            return self.anthropic_api_key.get_secret_value()
        if provider == "openai" and self.openai_api_key is not None:
            return self.openai_api_key.get_secret_value()
        return None

    def public_safe_dict(self) -> dict[str, object]:
        """Return non-secret config details suitable for health/debug output."""

        return {
            "app_env": self.app_env.value,
            "database_url": self.database_url,
            "auth_provider": self.auth_provider.value,
            "anthropic_configured": self.anthropic_api_key is not None,
            "anthropic_model": self.anthropic_model,
            "anthropic_fast_model": self.anthropic_fast_model,
            "openai_configured": self.openai_api_key is not None,
            "openai_model": self.openai_model,
            "customer_story_index": self.customer_story_index,
            "customer_story_namespace": self.customer_story_namespace,
            "customer_story_rag_disabled": self.customer_story_rag_disabled,
            "enable_gpt_image": self.enable_gpt_image,
            "artifact_dir": str(self.artifact_dir),
        }


def load_settings() -> Settings:
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        load_dotenv(override=False)
    return Settings.from_env()
