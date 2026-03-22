from __future__ import annotations

import os

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer()
_TOKEN = os.environ.get("AGENT_BUS_TOKEN", "")


async def require_token(
    cred: HTTPAuthorizationCredentials = Depends(_bearer),
) -> None:
    if not _TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGENT_BUS_TOKEN not configured",
        )
    if cred.credentials != _TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
