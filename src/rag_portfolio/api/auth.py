from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    actor_id: str
    admin: bool
    scope_keys: tuple[str, ...]
    audience: str = "patient"


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    settings = request.app.state.settings
    supplied = credentials.credentials if credentials else ""
    admin = settings.admin_token.get_secret_value()
    reader = settings.service_token.get_secret_value()
    if supplied and admin and compare_digest(supplied, admin):
        return Principal(settings.tenant_id, "service-admin", True, ())
    if supplied and reader and compare_digest(supplied, reader):
        return Principal(
            settings.tenant_id, "service-reader", False, tuple(settings.read_scope_keys)
        )
    raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})


async def require_admin(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
    if not principal.admin:
        raise HTTPException(403, "admin_required")
    return principal
