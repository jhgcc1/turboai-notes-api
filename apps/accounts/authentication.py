"""Cookie-based JWT authentication."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


class CookieJWTAuthentication(JWTAuthentication):
    """Prefer Authorization header, then access_token httpOnly cookie."""

    def authenticate(self, request: Request) -> tuple[Any, Any] | None:
        header = self.get_header(request)
        if header is not None:
            return super().authenticate(request)

        raw_token = request.COOKIES.get(settings.ACCESS_COOKIE_NAME)
        if not raw_token:
            return None
        try:
            validated = self.get_validated_token(raw_token)
        except (InvalidToken, TokenError):
            return None
        return self.get_user(validated), validated
