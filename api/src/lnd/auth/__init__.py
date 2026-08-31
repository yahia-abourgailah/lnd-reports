"""Authentication: company SSO over OIDC, carried in a signed session cookie.

No local passwords exist anywhere in this package (BRD NFR-04). Version 1 has a
single L&D role, but the identity provider's claims are carried through intact
so row-level scoping is additive later rather than a restructure (NFR-05).
"""

from lnd.auth.principal import Principal
from lnd.auth.session import clear_session, read_session, write_session

__all__ = ["Principal", "clear_session", "read_session", "write_session"]
