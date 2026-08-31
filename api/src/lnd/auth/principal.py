"""The signed-in user, as carried in the session cookie."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Principal(BaseModel):
    """Claims we keep after a successful sign-in.

    Deliberately small. The subject and email are what identity resolution and
    the audit trail need; `raw_claims` keeps whatever else the IdP asserted so
    that adding row-level scoping later is a matter of reading a claim we
    already have, not of changing the login flow.
    """

    subject: str
    email: str
    name: str
    issued_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    raw_claims: dict[str, str] = Field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.name or self.email
