"""JSON formatter, fingerprint and DRF exception handler behaviour."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.test import APIClient

from config.exception_handler import turbo_exception_handler
from config.fingerprint import build_fingerprint, normalize_path
from config.middleware import ERROR_MARKER, JsonFormatter


def _record(level: int, message: str, **extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="apps.error",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_json_with_service_and_environment() -> None:
    payload = json.loads(JsonFormatter().format(_record(logging.INFO, "hello")))
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["service"]
    assert payload["environment"]


def test_formatter_marks_error_lines() -> None:
    payload = json.loads(JsonFormatter().format(_record(logging.ERROR, "boom")))
    assert payload["message"] == f"{ERROR_MARKER} boom"
    assert payload["level"] == "ERROR"


def test_formatter_does_not_double_mark() -> None:
    payload = json.loads(JsonFormatter().format(_record(logging.ERROR, f"{ERROR_MARKER} boom")))
    assert payload["message"].count(ERROR_MARKER) == 1


def test_formatter_promotes_extra_fields() -> None:
    record = _record(logging.INFO, "request", request_id="abc", duration_ms=12.5, status=200)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "abc"
    assert payload["duration_ms"] == 12.5
    assert payload["status"] == 200


def test_formatter_includes_traceback() -> None:
    try:
        raise ValueError("kaboom")
    except ValueError:
        record = _record(logging.ERROR, "failed")
        import sys

        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: kaboom" in payload["exc_info"]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/notes/42/", "/api/notes/{id}/"),
        ("/api/notes/", "/api/notes/"),
        ("/api/notes/1f0c8f22-0f4e-4a1b-9a3d-3c9d1e2b4a55/", "/api/notes/{uuid}/"),
        ("/api/notes/7", "/api/notes/{id}"),
    ],
)
def test_normalize_path(path: str, expected: str) -> None:
    assert normalize_path(path) == expected


def test_fingerprint_is_stable_across_ids() -> None:
    def raise_error() -> BaseException:
        try:
            raise ValueError("x")
        except ValueError as exc:
            return exc

    first = build_fingerprint(raise_error(), normalize_path("/api/notes/1/"), 500)
    second = build_fingerprint(raise_error(), normalize_path("/api/notes/2/"), 500)
    assert first == second


def test_fingerprint_differs_by_exception_type() -> None:
    assert build_fingerprint(ValueError("a"), "/api/notes/", 500) != build_fingerprint(
        KeyError("a"), "/api/notes/", 500
    )


def test_fingerprint_without_exception() -> None:
    assert build_fingerprint(None, "/api/notes/", 502)


def test_exception_handler_logs_client_errors_as_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="apps.error"):
        response = turbo_exception_handler(ValidationError("bad"), {"request": None, "view": None})
    assert response is not None
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert caplog.records[0].levelno == logging.WARNING
    assert caplog.records[0].fingerprint  # type: ignore[attr-defined]


def test_exception_handler_logs_server_errors_as_error(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="apps.error"):
        response = turbo_exception_handler(RuntimeError("boom"), {"request": None, "view": None})
    # DRF does not convert non-API exceptions; Django turns them into a 500.
    assert response is None
    assert caplog.records[0].levelno == logging.ERROR
    assert caplog.records[0].exc_info is not None


def test_exception_handler_reports_404_as_client_error(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="apps.error"):
        turbo_exception_handler(NotFound(), {"request": None, "view": None})
    assert caplog.records[0].status == status.HTTP_404_NOT_FOUND  # type: ignore[attr-defined]


@pytest.mark.django_db
def test_request_middleware_sets_request_id_header() -> None:
    response = APIClient().get(reverse("health"))
    assert response["X-Request-ID"]


@pytest.mark.django_db
def test_request_middleware_echoes_incoming_request_id() -> None:
    response = APIClient().get(reverse("health"), HTTP_X_REQUEST_ID="trace-me")
    assert response["X-Request-ID"] == "trace-me"
