"""Application settings.

Every environment-specific value and every credential arrives as an environment
variable. Nothing is read from a file at runtime and nothing has a secret
default (BRD NFR-11).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,  # containers get real env vars; .env is a compose concern
        extra="ignore",
        frozen=True,
    )

    # -- core ---------------------------------------------------------------
    environment: Environment = Environment.DEV
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8080"
    web_base_url: str = "http://localhost:8080"

    # -- data ---------------------------------------------------------------
    database_url: str = "postgresql+psycopg://lnd:lnd@db:5432/lnd"
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    redis_url: str = "redis://redis:6379/0"

    # -- session ------------------------------------------------------------
    session_secret: str = Field(default="", repr=False)
    session_cookie_name: str = "lnd_session"
    session_max_age_seconds: int = 8 * 60 * 60

    # -- OIDC ---------------------------------------------------------------
    oidc_discovery_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = Field(default="", repr=False)
    oidc_scopes: str = "openid profile email"
    oidc_redirect_uri: str = "http://localhost:8080/v1/auth/callback"

    # -- development-only auth shortcut -------------------------------------
    auth_dev_bypass: bool = False
    auth_dev_user_email: str = "dev@example.com"
    auth_dev_user_name: str = "Dev User"

    # -- derived ------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def cookies_require_https(self) -> bool:
        """Secure cookies everywhere a browser could reach us over TLS."""
        return self.environment is not Environment.DEV

    @property
    def oidc_configured(self) -> bool:
        return bool(self.oidc_discovery_url and self.oidc_client_id and self.oidc_client_secret)

    @property
    def scope_list(self) -> list[str]:
        return self.oidc_scopes.split()

    # -- guards -------------------------------------------------------------
    @model_validator(mode="after")
    def _refuse_unsafe_combinations(self) -> Settings:
        """Fail at startup rather than serve something unsafe.

        The dev bypass exists so week 1 is not blocked on the app registration
        (Q-14). It must be impossible to leave switched on by accident anywhere
        a real person could reach, so it is refused outside dev and the process
        does not start.
        """
        if self.auth_dev_bypass and self.environment is not Environment.DEV:
            raise ValueError(
                f"AUTH_DEV_BYPASS is enabled but ENVIRONMENT={self.environment}. "
                "The bypass is permitted in dev only."
            )

        if self.environment is not Environment.DEV:
            if not self.session_secret:
                raise ValueError("SESSION_SECRET is required outside dev.")
            if len(self.session_secret) < 32:
                raise ValueError("SESSION_SECRET must be at least 32 characters.")
            if not self.oidc_configured:
                raise ValueError(
                    "OIDC_DISCOVERY_URL, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET are "
                    "required outside dev — there are no local passwords (NFR-04)."
                )

        if self.is_production and not self.oidc_redirect_uri.startswith("https://"):
            raise ValueError("OIDC_REDIRECT_URI must be https:// in production.")

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so validation runs exactly once."""
    return Settings()
