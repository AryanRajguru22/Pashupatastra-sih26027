"""Process-wide handle on the AuthService (None in demo mode).

Set once by the composition root. Request-time code asks here whether
authentication is enforced; it never builds an AuthService itself.
"""

from __future__ import annotations

from typing import Optional

from backend.app.auth.service import AuthService

_service: Optional[AuthService] = None


def set_auth_service(service: Optional[AuthService]) -> None:
    global _service
    _service = service


def get_auth_service() -> Optional[AuthService]:
    return _service


def auth_enforced() -> bool:
    return _service is not None


__all__ = ["auth_enforced", "get_auth_service", "set_auth_service"]
