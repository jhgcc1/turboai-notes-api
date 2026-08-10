"""Tests for the browser client-error intake endpoint."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.observability.views import _client_fingerprint


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def user(db: None) -> User:
    return User.objects.create_user(
        username="obs@example.com", email="obs@example.com", password="x"
    )


def test_client_fingerprint_is_stable() -> None:
    a = _client_fingerprint("boom", "https://app.example/notes/42/", "window.onerror")
    b = _client_fingerprint("boom", "https://app.example/notes/99/", "window.onerror")
    assert a == b
    assert len(a) == 16


@pytest.mark.django_db
def test_client_error_records_json_error(api: APIClient, caplog: pytest.LogCaptureFixture) -> None:
    url = reverse("observability-client-error")
    with caplog.at_level("ERROR", logger="apps.observability"):
        res = api.post(
            url,
            {
                "message": "ReferenceError: x is not defined",
                "stack": "ReferenceError: x is not defined\n    at Object.<anonymous> (app.js:1:1)",
                "url": "https://example.com/notes",
                "user_agent": "Vitest",
                "source": "window.onerror",
                "request_id": "abc-123",
            },
            format="json",
        )
    assert res.status_code == status.HTTP_202_ACCEPTED
    assert res.data["detail"] == "recorded"
    assert len(res.data["fingerprint"]) == 16
    assert any("client_error" in r.message for r in caplog.records)


@pytest.mark.django_db
def test_client_error_rejects_empty_message(api: APIClient) -> None:
    res = api.post(reverse("observability-client-error"), {"message": ""}, format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_client_error_rejects_oversized_stack(api: APIClient) -> None:
    res = api.post(
        reverse("observability-client-error"),
        {"message": "boom", "stack": "x" * 9_000},
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_client_error_anonymous_allowed(api: APIClient) -> None:
    res = api.post(
        reverse("observability-client-error"),
        {"message": "TypeError: failed", "source": "unhandledrejection"},
        format="json",
    )
    assert res.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.django_db
def test_client_error_cookie_auth_requires_csrf(user: User) -> None:
    strict = APIClient(enforce_csrf_checks=True)
    refresh = RefreshToken.for_user(user)
    strict.cookies["access_token"] = str(refresh.access_token)

    denied = strict.post(
        reverse("observability-client-error"),
        {"message": "should fail csrf"},
        format="json",
    )
    assert denied.status_code == status.HTTP_403_FORBIDDEN

    csrf_res = strict.get(reverse("auth-csrf"))
    token = csrf_res.data["csrfToken"]
    ok = strict.post(
        reverse("observability-client-error"),
        {"message": "csrf ok"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert ok.status_code == status.HTTP_202_ACCEPTED


@pytest.mark.django_db
def test_client_error_caps_user_agent_in_log(
    api: APIClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("ERROR", logger="apps.observability"):
        api.post(
            reverse("observability-client-error"),
            {"message": "ua test", "user_agent": "U" * 800},
            format="json",
        )
    record = next(r for r in caplog.records if "client_error" in r.message)
    assert len(getattr(record, "user_agent", "")) <= 512
