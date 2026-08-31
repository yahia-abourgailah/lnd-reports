"""Request dependencies for authentication."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from lnd.auth.principal import Principal
from lnd.auth.session import read_session


def current_principal(request: Request) -> Principal | None:
    """The signed-in user, or None. Never rejects."""
    return read_session(request)


def require_user(request: Request) -> Principal:
    """The signed-in user, or 401.

    Version 1 has a single L&D permission set, so membership is the whole
    authorisation check. When row-level scoping arrives it becomes a second
    dependency layered on this one rather than a change here.
    """
    principal = read_session(request)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


CurrentUser = Annotated[Principal, Depends(require_user)]
OptionalUser = Annotated[Principal | None, Depends(current_principal)]
