"""Auth API views."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth import login as django_login
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
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


class AuthThrottle(AnonRateThrottle):
    scope = "auth"


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        return Response({"detail": "CSRF cookie set"})


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
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
    authentication_classes: list = []
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
        raw = request.COOKIES.get("refresh_token") or request.data.get("refresh")
        if raw:
            try:
                token = RefreshToken(raw)
                token.blacklist()
            except (TokenError, InvalidToken, AttributeError):
                pass
        response = Response({"detail": "Logged out"})
        return clear_auth_cookies(response)


class RefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [AuthThrottle]

    def post(self, request: Request) -> Response:
        raw = request.COOKIES.get("refresh_token") or request.data.get("refresh")
        if not raw:
            return Response(
                {"detail": "Refresh token missing"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        try:
            old = RefreshToken(raw)
            user_id = old["user_id"]
            old.blacklist()
            user = get_user_model().objects.get(pk=user_id)
            new_refresh = RefreshToken.for_user(user)
        except (TokenError, InvalidToken, get_user_model().DoesNotExist, KeyError):
            return Response(
                {"detail": "Invalid refresh token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        response = Response({"detail": "Token refreshed"})
        return set_auth_cookies(response, str(new_refresh.access_token), str(new_refresh))


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)
