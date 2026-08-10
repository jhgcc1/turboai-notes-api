"""Auth API views."""

from __future__ import annotations

import logging
from typing import Any, cast

from django.contrib.auth import get_user_model
from django.contrib.auth import login as django_login
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.cookies import clear_auth_cookies, set_auth_cookies
from apps.accounts.serializers import LoginSerializer, RegisterSerializer, UserSerializer
from apps.notes.services import ensure_default_categories

NoAuth: list[type[BaseAuthentication]] = []

logger = logging.getLogger("apps.accounts")


def _refresh_from_body(request: Request) -> str | None:
    """Read ``refresh`` from the JSON body, tolerating non-dict payloads."""
    data: Any = request.data
    return data.get("refresh") if isinstance(data, dict) else None


class AuthThrottle(AnonRateThrottle):
    scope = "auth"


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = NoAuth

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = NoAuth

    def get(self, request: Request) -> Response:
        # Return the token in the body too: the SPA cannot read a cross-origin
        # csrftoken cookie via document.cookie (frontend CF ≠ API CF).
        return Response({"detail": "CSRF cookie set", "csrfToken": get_token(request)})


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = NoAuth
    throttle_classes = [AuthThrottle]

    def post(self, request: Request) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        ensure_default_categories(user)
        refresh = RefreshToken.for_user(user)
        response = Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
        return set_auth_cookies(response, str(refresh.access_token), str(refresh))


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = NoAuth
    throttle_classes = [AuthThrottle]

    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        django_login(request, user)
        ensure_default_categories(user)
        refresh = RefreshToken.for_user(user)
        response = Response(UserSerializer(user).data)
        return set_auth_cookies(response, str(refresh.access_token), str(refresh))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        raw = request.COOKIES.get("refresh_token") or _refresh_from_body(request)
        if raw:
            try:
                token = RefreshToken(raw)  # type: ignore[arg-type]
                token.blacklist()
            except (TokenError, InvalidToken, AttributeError) as exc:
                # Logout stays idempotent — cookies are cleared either way — but
                # a token that cannot be blacklisted is worth an auth trail.
                logger.warning(
                    "logout_blacklist_failed",
                    extra={
                        "request_id": getattr(request, "request_id", "-"),
                        "error_type": type(exc).__name__,
                    },
                )
        response = Response({"detail": "Logged out"})
        return clear_auth_cookies(response)


class RefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = NoAuth
    throttle_classes = [AuthThrottle]

    def post(self, request: Request) -> Response:
        raw = request.COOKIES.get("refresh_token") or _refresh_from_body(request)
        if not raw:
            return Response(
                {"detail": "Refresh token missing"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            old = RefreshToken(raw)  # type: ignore[arg-type]
            user_id = old["user_id"]
            old.blacklist()
            user = get_user_model().objects.get(pk=user_id)
            new_refresh = RefreshToken.for_user(user)
        except (TokenError, InvalidToken, get_user_model().DoesNotExist, KeyError) as exc:
            # A spike here means expired sessions, a rotation bug, or token
            # replay — all indistinguishable from silence before this log.
            logger.warning(
                "refresh_rejected",
                extra={
                    "request_id": getattr(request, "request_id", "-"),
                    "error_type": type(exc).__name__,
                },
            )
            return Response(
                {"detail": "Invalid refresh token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        response = Response({"detail": "Token refreshed"})
        return set_auth_cookies(response, str(new_refresh.access_token), str(new_refresh))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        user = cast(User, request.user)
        return Response(UserSerializer(user).data)
