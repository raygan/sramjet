"""HTTP Basic auth dependencies.

Two independent auth surfaces:
  - UI auth:     protects the dashboard and REST API
  - WebDAV auth: protects the sync endpoints (requires matching credentials in RetroArch)

Each surface is enabled only when both its env vars are set (AUTH_UI_USERNAME +
AUTH_UI_PASSWORD, or AUTH_WEBDAV_USERNAME + AUTH_WEBDAV_PASSWORD). If neither
var in a pair is set, that surface remains open — existing behaviour is unchanged.

secrets.compare_digest is used for all comparisons to prevent timing attacks.
"""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import app.config

_security = HTTPBasic(auto_error=False)


def _check(
    credentials: HTTPBasicCredentials | None,
    expected_username: str | None,
    expected_password: str | None,
    enabled: bool,
) -> None:
    if not enabled:
        return
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )
    valid_username = secrets.compare_digest(credentials.username, expected_username)
    valid_password = secrets.compare_digest(credentials.password, expected_password)
    if not (valid_username and valid_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Basic"},
        )


def require_ui_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    _check(credentials, app.config.UI_USERNAME, app.config.UI_PASSWORD, app.config.UI_AUTH_ENABLED)


def require_webdav_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    _check(
        credentials,
        app.config.WEBDAV_USERNAME,
        app.config.WEBDAV_PASSWORD,
        app.config.WEBDAV_AUTH_ENABLED,
    )
