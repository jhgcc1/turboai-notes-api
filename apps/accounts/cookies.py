"""Auth cookie helpers."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal, cast

from django.conf import settings
from django.http import HttpResponse

SameSite = Literal["Lax", "Strict", "None"]


def _max_age(lifetime_key: str) -> int:
    lifetime = cast(timedelta, settings.SIMPLE_JWT[lifetime_key])
    return int(lifetime.total_seconds())


def set_auth_cookies[ResponseT: HttpResponse](
    response: ResponseT, access: str, refresh: str
) -> ResponseT:
    domain: str | None = settings.COOKIE_DOMAIN or None
    samesite = cast(SameSite, settings.COOKIE_SAMESITE)

    response.set_cookie(
        settings.ACCESS_COOKIE_NAME,
        access,
        max_age=_max_age("ACCESS_TOKEN_LIFETIME"),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=samesite,
        path="/",
        domain=domain,
    )
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        refresh,
        max_age=_max_age("REFRESH_TOKEN_LIFETIME"),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=samesite,
        path="/",
        domain=domain,
    )
    return response


def clear_auth_cookies[ResponseT: HttpResponse](response: ResponseT) -> ResponseT:
    domain: str | None = settings.COOKIE_DOMAIN or None
    samesite = cast(SameSite, settings.COOKIE_SAMESITE)
    response.delete_cookie(settings.ACCESS_COOKIE_NAME, path="/", domain=domain, samesite=samesite)
    response.delete_cookie(settings.REFRESH_COOKIE_NAME, path="/", domain=domain, samesite=samesite)
    return response
