from __future__ import annotations

import hmac

from fastapi import HTTPException, status


def require_bearer(authorization: str | None, expected_token: bytes) -> None:
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    try:
        candidate = authorization[len(prefix) :].encode("ascii")
    except UnicodeEncodeError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from error
    if not hmac.compare_digest(candidate, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
