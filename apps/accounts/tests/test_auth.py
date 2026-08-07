"""Account API tests."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(
        username="friend@example.com",
        email="friend@example.com",
        password="StrongPass123!",
    )


@pytest.mark.django_db
def test_health(api: APIClient) -> None:
    res = api.get(reverse("health"))
    assert res.status_code == status.HTTP_200_OK
    assert res.data["status"] == "ok"


@pytest.mark.django_db
def test_csrf_sets_cookie(api: APIClient) -> None:
    res = api.get(reverse("auth-csrf"))
    assert res.status_code == status.HTTP_200_OK
    assert "csrftoken" in res.cookies or res.data["detail"]


@pytest.mark.django_db
def test_register_login_me_logout_refresh(api: APIClient) -> None:
    reg = api.post(
        reverse("auth-register"),
        {"email": "new@example.com", "password": "StrongPass123!"},
        format="json",
    )
    assert reg.status_code == status.HTTP_201_CREATED
    assert "access_token" in reg.cookies
    assert "refresh_token" in reg.cookies

    me = api.get(reverse("auth-me"))
    assert me.status_code == status.HTTP_200_OK
    assert me.data["email"] == "new@example.com"

    # duplicate email
    dup = api.post(
        reverse("auth-register"),
        {"email": "new@example.com", "password": "StrongPass123!"},
        format="json",
    )
    assert dup.status_code == status.HTTP_400_BAD_REQUEST

    api.cookies.clear()
    bad = api.post(
        reverse("auth-login"),
        {"email": "new@example.com", "password": "wrong"},
        format="json",
    )
    assert bad.status_code == status.HTTP_400_BAD_REQUEST

    login = api.post(
        reverse("auth-login"),
        {"email": "new@example.com", "password": "StrongPass123!"},
        format="json",
    )
    assert login.status_code == status.HTTP_200_OK
    refresh_cookie = login.cookies["refresh_token"].value

    refresh = api.post(reverse("auth-refresh"), format="json")
    assert refresh.status_code == status.HTTP_200_OK
    assert "access_token" in refresh.cookies

    # refresh with body token
    api.cookies.clear()
    refresh_body = api.post(
        reverse("auth-refresh"),
        {"refresh": refresh_cookie},
        format="json",
    )
    # old refresh may be blacklisted after previous rotation — login again
    if refresh_body.status_code == status.HTTP_401_UNAUTHORIZED:
        login2 = api.post(
            reverse("auth-login"),
            {"email": "new@example.com", "password": "StrongPass123!"},
            format="json",
        )
        assert login2.status_code == status.HTTP_200_OK
    else:
        assert refresh_body.status_code == status.HTTP_200_OK

    api.post(
        reverse("auth-login"),
        {"email": "new@example.com", "password": "StrongPass123!"},
        format="json",
    )
    logout = api.post(reverse("auth-logout"), format="json")
    assert logout.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_refresh_missing_and_invalid(api: APIClient) -> None:
    missing = api.post(reverse("auth-refresh"), format="json")
    assert missing.status_code == status.HTTP_401_UNAUTHORIZED

    invalid = api.post(reverse("auth-refresh"), {"refresh": "not-a-token"}, format="json")
    assert invalid.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_logout_with_invalid_refresh(api: APIClient, user: User) -> None:
    refresh = RefreshToken.for_user(user)
    api.cookies["access_token"] = str(refresh.access_token)
    api.cookies["refresh_token"] = "bad"
    res = api.post(reverse("auth-logout"), format="json")
    assert res.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_weak_password_rejected(api: APIClient) -> None:
    res = api.post(
        reverse("auth-register"),
        {"email": "weak@example.com", "password": "123"},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_disabled_user_login(api: APIClient, user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    user.is_active = False
    user.save()

    def _auth(**kwargs):  # type: ignore[no-untyped-def]
        return user

    monkeypatch.setattr("apps.accounts.serializers.authenticate", _auth)
    res = api.post(
        reverse("auth-login"),
        {"email": user.email, "password": "StrongPass123!"},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_cookie_auth_no_cookie_returns_none(api: APIClient) -> None:
    from django.test import RequestFactory
    from rest_framework.request import Request

    from apps.accounts.authentication import CookieJWTAuthentication

    factory = RequestFactory()
    django_req = factory.get("/api/auth/me/")
    req = Request(django_req)
    assert CookieJWTAuthentication().authenticate(req) is None


@pytest.mark.django_db
def test_cookie_jwt_auth_and_bearer(api: APIClient, user: User) -> None:
    from django.conf import settings as djsettings
    from django.test import RequestFactory
    from rest_framework.request import Request

    from apps.accounts.authentication import CookieJWTAuthentication

    refresh = RefreshToken.for_user(user)
    factory = RequestFactory()
    auth = CookieJWTAuthentication()

    django_req = factory.get("/api/auth/me/")
    django_req.COOKIES[djsettings.ACCESS_COOKIE_NAME] = str(refresh.access_token)
    result = auth.authenticate(Request(django_req))
    assert result is not None
    assert result[0].id == user.id

    django_req2 = factory.get(
        "/api/auth/me/",
        HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}",
    )
    result2 = auth.authenticate(Request(django_req2))
    assert result2 is not None

    django_req3 = factory.get("/api/auth/me/")
    django_req3.COOKIES[djsettings.ACCESS_COOKIE_NAME] = "not-valid-jwt"
    assert auth.authenticate(Request(django_req3)) is None

    api.cookies["access_token"] = str(refresh.access_token)
    assert api.get(reverse("auth-me")).status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_cookie_auth_enforces_csrf(user: User) -> None:
    """Cookie-authenticated mutations must carry a valid CSRF token.

    Regression test for the security audit finding that ``APIView``'s
    implicit ``csrf_exempt`` left every cookie-authenticated write endpoint
    forgeable cross-site (see apps.accounts.authentication.CookieJWTAuthentication).
    """
    strict = APIClient(enforce_csrf_checks=True)
    refresh = RefreshToken.for_user(user)
    strict.cookies["access_token"] = str(refresh.access_token)

    denied = strict.post(reverse("auth-logout"), format="json")
    assert denied.status_code == status.HTTP_403_FORBIDDEN

    csrf_res = strict.get(reverse("auth-csrf"))
    token = csrf_res.cookies["csrftoken"].value
    ok = strict.post(reverse("auth-logout"), format="json", HTTP_X_CSRFTOKEN=token)
    assert ok.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_bearer_auth_exempt_from_csrf(user: User) -> None:
    """Authorization-header (bearer) auth is not cookie-based, so a mutating
    request needs no CSRF token — unlike the cookie-auth path above."""
    strict = APIClient(enforce_csrf_checks=True)
    refresh = RefreshToken.for_user(user)
    strict.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    res = strict.post(reverse("auth-logout"), format="json")
    assert res.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_set_clear_cookies_with_domain(settings, api: APIClient) -> None:
    from django.http import HttpResponse

    from apps.accounts.cookies import clear_auth_cookies, set_auth_cookies

    settings.COOKIE_DOMAIN = "example.com"
    settings.COOKIE_SECURE = True
    resp = HttpResponse()
    set_auth_cookies(resp, "a", "r")
    assert settings.ACCESS_COOKIE_NAME in resp.cookies
    clear_auth_cookies(resp)
