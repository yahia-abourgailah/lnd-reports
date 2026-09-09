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

    # -- sync ---------------------------------------------------------------
    # An incremental pull resumes from the last successful position less this
    # overlap. Source clocks are not our clock, and a record written at the
    # exact moment of the last watermark must not fall between two windows.
    # Re-reading a few minutes is free: the transform is idempotent (FR-A10).
    sync_overlap_seconds: int = 300
    # A run still marked `running` after this long was abandoned — the worker
    # was killed rather than the sync being slow. Comfortably above Celery's
    # 30-minute hard time limit, so a merely slow sync is never reaped out from
    # under itself.
    sync_abandoned_after_seconds: int = 2400
    # Freshness lag past which the badge turns amber and the alert fires
    # (NFR-03). Two missed 30-minute runs, so a single failure that the next
    # run recovers from does not wake anyone.
    freshness_stale_after_seconds: int = 3600

    # -- retry and the circuit breaker --------------------------------------
    # Attempts within one sync before it gives up and the run is recorded
    # failed. Delays grow base * multiplier ** (attempt - 1), capped, jittered.
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 1.0
    retry_max_delay_seconds: float = 30.0
    # Spread as a fraction of the delay. Without it every entity that failed in
    # the same beat tick retries in lockstep and hits a recovering source
    # simultaneously — the thundering herd that stops it recovering.
    retry_jitter: float = 0.25

    # Consecutive failed runs for one source, ignoring skips, before it stops
    # being called. Counted across the source's entities and reset by any
    # success, so a whole-source outage trips it within a single beat tick
    # while one flaky entity among healthy ones never does.
    breaker_failure_threshold: int = 3
    # How long the source is left alone before one trial call is allowed.
    breaker_cooldown_seconds: int = 600

    # -- alerting -----------------------------------------------------------
    # How often an unresolved problem is repeated. A three-day outage evaluated
    # every 15 minutes would otherwise send 288 messages, and the practical
    # result of that is a muted channel and an unread real alert.
    alert_renotify_seconds: int = 21600
    # Soft deletes in one nightly reconcile above which the difference stops
    # being routine. Absolute rather than proportional: the dataset is small
    # and known (~15,000 rows at the ceiling), so a fixed number is easier to
    # reason about than a percentage of a moving total.
    alert_reconcile_delete_threshold: int = 25
    # How far back a reconcile counts as current news.
    alert_reconcile_window_seconds: int = 86400
    # How long a record may be excluded from figures before somebody is told
    # rather than left to notice. Seven days: long enough that a fix authored
    # the same afternoon never raises an alert, short enough that nothing sits
    # out of the numbers for a whole reporting cycle unremarked.
    alert_exclusion_age_seconds: int = 7 * 86400

    # -- calendar -----------------------------------------------------------
    # The month the company's fiscal year starts in. Finance confirmed
    # January (2026-09-06), so the fiscal calendar is the calendar calendar and
    # fiscal_year/fiscal_quarter equal their civil counterparts.
    #
    # The setting stays, and so does the offset arithmetic behind it. It costs
    # nothing while the answer is 1, and the alternative — hardcoding the
    # calendar year because that is what we were told once — is how a reporting
    # layer comes to need surgery the year a subsidiary is acquired. Changing
    # it does not rewrite dim_date on its own; see transform.dates.
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)

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

    # -- source systems -----------------------------------------------------
    # Blank until the CRM team issues read-only API access (risk R-01). The API
    # starts fine without them; only the sync tasks need them, and they say so
    # clearly when they run.
    crm_base_url: str = ""
    #: Static shared secret, sent in the X-Learning-Key header. Not a JWT, does
    #: not expire, never leaves the server side. Issued by the CRM team as
    #: LEARNING_INTEGRATION_SERVICE_KEY on their deployment.
    crm_service_key: str = Field(default="", repr=False)
    crm_timeout_seconds: float = 60.0
    #: Each entry carries a program's whole roster and every answer on it, so a
    #: big page is a big response. 10-25 is the documented comfortable size.
    crm_per_page: int = 10
    #: The roster is small rows and many of them — 1,427 active employees, so
    #: at the programs page size that is 143 requests against a 120/min limit.
    #: A program is ~90 KB and a roster row is a few hundred bytes, so the two
    #: endpoints want opposite page sizes.
    crm_employee_per_page: int = 200
    #: Which employees the roster pull asks for. `active` is the participation
    #: rate denominator — people who could attend. Blank fetches everyone,
    #: including leavers, which the coverage report may want for a period they
    #: were still employed in.
    crm_employee_status: str = "active"
    # The `filter[...]` key the CRM accepts for "changed since", if it accepts
    # one at all. Empty means it does not, and the incremental sync degrades to
    # fetching everything each pass — which is correct, just wasteful: nothing
    # re-lands, because the unique constraint on (source, entity, source_id,
    # payload_hash) makes an unchanged record a no-op. Set it and the same
    # runner narrows the request, with no other change.
    crm_changed_since_filter: str = ""

    #: Save every source response verbatim as a contract fixture. Off by
    #: default — an accidental recording against production would write real
    #: employee payloads into the repository.
    source_record_fixtures: bool = False

    # -- report delivery ----------------------------------------------------
    # SMTP is off until a host is configured, and that is the switch rather
    # than an environment check: staging must be able to send to a mailbox
    # somebody is watching, and production must be able to stop sending
    # without a deploy when a relay is being moved.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = Field(default="", repr=False)
    #: STARTTLS on the standard submission port. Implicit TLS (465) is
    #: `smtp_ssl`; the two are mutually exclusive and the guard below says so.
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_timeout_seconds: float = 30.0

    report_from_address: str = "lnd-analytics@example.com"
    #: Comma-separated. A list rather than one address because the monthly
    #: report goes to a group, and a distribution list nobody can enumerate is
    #: a delivery nobody can audit.
    report_recipients: str = ""
    #: Attach the PDF alongside the workbook. Both are kept as editions either
    #: way; this only decides what leaves in the mail.
    report_attach_pdf: bool = True

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

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host)

    @property
    def recipient_list(self) -> list[str]:
        """The report's recipients, trimmed and de-blanked.

        A trailing comma in an environment variable is not an empty recipient,
        and an SMTP server handed one rejects the whole message.
        """
        return [address.strip() for address in self.report_recipients.split(",") if address.strip()]

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

        if self.source_record_fixtures and self.is_production:
            raise ValueError(
                "SOURCE_RECORD_FIXTURES writes source payloads to disk and must "
                "never be enabled in production — those payloads contain PII."
            )

        if self.smtp_starttls and self.smtp_ssl:
            raise ValueError(
                "SMTP_STARTTLS and SMTP_SSL are mutually exclusive: STARTTLS upgrades "
                "a plain connection, SSL wraps one from the first byte."
            )

        encrypted = self.smtp_starttls or self.smtp_ssl
        if self.smtp_configured and self.is_production and not encrypted:
            raise ValueError(
                "SMTP in production must use STARTTLS or SSL. The monthly report "
                "carries employee names."
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so validation runs exactly once."""
    return Settings()
