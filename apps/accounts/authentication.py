"""Cookie-based JWT authentication."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import exceptions
from rest_framework.authentication import CSRFCheck
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


class CookieJWTAuthentication(JWTAuthentication):
    """Prefer Authorization header, then access_token httpOnly cookie.

    DRF's ``APIView`` disables Django's global CSRF middleware for every view
    (see ``rest_framework.views.APIView.as_view``), so cookie-based auth must
    enforce CSRF itself — otherwise any state-changing endpoint authenticated
    via the httpOnly cookie would be forgeable cross-site. Bearer-token auth
    (mobile/CLI clients sending an explicit Authorization header) is not
    cookie-based and is not CSRF-vulnerable, so it is exempt, matching
    ``rest_framework.authentication.SessionAuthentication.enforce_csrf``.
    """

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
        self.enforce_csrf(request)
        return self.get_user(validated), validated

    def enforce_csrf(self, request: Request) -> None:
        # Same pattern as rest_framework.authentication.SessionAuthentication;
        # django-stubs' get_response typing doesn't model this internal usage.
        check = CSRFCheck(lambda r: None)  # type: ignore[arg-type]
        check.process_request(request)
        reason = check.process_view(request, None, (), {})  # type: ignore[arg-type]
        if reason:
            raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")
