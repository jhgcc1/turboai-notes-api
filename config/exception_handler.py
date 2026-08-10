"""DRF exception handler that turns every API failure into a structured log.

Without this, DRF converts exceptions into responses silently: a 400 from a
serializer or a 403 from a permission never reaches a logger, so the only
errors visible in CloudWatch were unhandled 500s caught by the middleware.
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from config.fingerprint import build_fingerprint, normalize_path

logger = logging.getLogger("apps.error")

# 4xx means the caller sent something invalid. Logging those at ERROR would
# make the metric filter (and the triage automation behind it) fire on ordinary
# bad requests, so they stay at WARNING and remain searchable without paging.
SERVER_ERROR_THRESHOLD = 500


def _context_payload(context: dict[str, Any]) -> dict[str, Any]:
    request = context.get("request")
    view = context.get("view")
    path = getattr(request, "path", "-")
    return {
        "request_id": getattr(request, "request_id", "-"),
        "method": getattr(request, "method", "-"),
        "path": path,
        "route": normalize_path(path),
        "view": type(view).__name__ if view is not None else "-",
        "user_id": getattr(getattr(request, "user", None), "id", None),
        "environment": settings.ENVIRONMENT,
    }


def turbo_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    """Log the failure, then defer to DRF for the actual response shape."""
    response = drf_exception_handler(exc, context)
    payload = _context_payload(context)
    status_code = response.status_code if response is not None else SERVER_ERROR_THRESHOLD
    payload["status"] = status_code
    payload["error_type"] = type(exc).__name__
    payload["fingerprint"] = build_fingerprint(exc, payload["route"], status_code)

    if status_code >= SERVER_ERROR_THRESHOLD:
        # exc_info drives the traceback the triage Lambda feeds to the LLM.
        logger.error(
            "unhandled_exception %s at %s",
            payload["error_type"],
            payload["route"],
            exc_info=exc,
            extra=payload,
        )
        # Prevent RequestLoggingMiddleware from emitting a second, traceback-less
        # ERROR for the same request (that would dilute fingerprint grouping).
        request = context.get("request")
        if request is not None:
            request._error_logged = True
    else:
        logger.warning(
            "client_error %s at %s -> %s",
            payload["error_type"],
            payload["route"],
            status_code,
            extra=payload,
        )
    return response
