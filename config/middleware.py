"""JSON log formatter and request logging middleware."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.deprecation import MiddlewareMixin

from config.fingerprint import normalize_path

ERROR_MARKER = "[ERROR]"

# Attributes ``logging`` puts on every record. Anything outside this set came
# from an ``extra={...}`` at the call site and belongs in the JSON payload.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, shaped for CloudWatch Logs metric filters.

    ``service``/``environment`` are read from the process environment rather
    than ``django.conf.settings``: ``LOGGING`` is applied while settings are
    still being imported, so the formatter must not depend on Django being
    ready.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.service = os.getenv("SERVICE_NAME", "turboai-notes-api")
        self.environment = os.getenv("ENVIRONMENT", "local")

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        # Alarms filter on `$.level`, but the marker keeps errors greppable in
        # plain-text views (ECS console, `docker logs`, `logs_tail`).
        if record.levelno >= logging.ERROR and ERROR_MARKER not in message:
            message = f"{ERROR_MARKER} {message}"

        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "time": self.formatTime(record, self.datefmt),
            "service": self.service,
            "environment": self.environment,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        else:
            # Browser client-error intake attaches a string stack via
            # ``extra={"stack": ...}``. Promote it to ``exc_info`` so the triage
            # Lambda Logs Insights query always finds a usable traceback field.
            stack = getattr(record, "stack", None)
            if isinstance(stack, str) and stack:
                payload["exc_info"] = stack
        return json.dumps(payload, default=str)


class RequestLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request: HttpRequest) -> None:
        request.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))  # type: ignore[attr-defined]
        request._start_time = time.monotonic()  # type: ignore[attr-defined]

    def process_exception(self, request: HttpRequest, exception: Exception) -> None:
        """Capture uncaught exceptions with a full traceback.

        DRF's exception handler already logs API failures; this covers the
        non-DRF path so CloudWatch always gets ``exc_info`` for triage.
        """
        if getattr(request, "_error_logged", False):
            return None
        from config.fingerprint import build_fingerprint

        path = request.path
        route = normalize_path(path)
        payload = {
            "request_id": getattr(request, "request_id", "-"),
            "method": request.method,
            "path": path,
            "route": route,
            "status": 500,
            "error_type": type(exception).__name__,
            "fingerprint": build_fingerprint(exception, route, 500),
            "user_id": getattr(getattr(request, "user", None), "id", None),
        }
        logging.getLogger("apps.error").error(
            "unhandled_exception %s at %s",
            payload["error_type"],
            route,
            exc_info=exception,
            extra=payload,
        )
        request._error_logged = True  # type: ignore[attr-defined]
        return None

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        request_id = getattr(request, "request_id", "-")
        duration_ms = 0.0
        if hasattr(request, "_start_time"):
            duration_ms = (time.monotonic() - request._start_time) * 1000
        response["X-Request-ID"] = request_id
        user_id = getattr(getattr(request, "user", None), "id", None)
        logging.getLogger("apps.request").info(
            "%s %s -> %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "duration_ms": round(duration_ms, 2),
                "status": response.status_code,
                "method": request.method,
                "path": request.path,
                "route": normalize_path(request.path),
                "user_id": user_id,
            },
        )
        # Skip when exception_handler / process_exception already emitted a
        # fingerprint+traceback ERROR — a bare 500 line would dilute triage.
        if response.status_code >= 500 and not getattr(request, "_error_logged", False):
            route = normalize_path(request.path)
            logging.getLogger("apps.error").error(
                "server_error path=%s status=%s",
                request.path,
                response.status_code,
                extra={
                    "request_id": request_id,
                    "status": response.status_code,
                    "method": request.method,
                    "path": request.path,
                    "route": route,
                    "user_id": user_id,
                    "error_type": "Http500",
                    "fingerprint": f"http500:{route}:{response.status_code}",
                },
            )
        return response
