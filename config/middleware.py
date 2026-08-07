"""JSON log formatter and request logging middleware."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils.deprecation import MiddlewareMixin


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "time": self.formatTime(record, self.datefmt),
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class RequestLoggingMiddleware(MiddlewareMixin):
    def process_request(self, request: HttpRequest) -> None:
        request.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))  # type: ignore[attr-defined]
        request._start_time = time.monotonic()  # type: ignore[attr-defined]

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        request_id = getattr(request, "request_id", "-")
        duration_ms = 0.0
        if hasattr(request, "_start_time"):
            duration_ms = (time.monotonic() - request._start_time) * 1000  # type: ignore[attr-defined]
        response["X-Request-ID"] = request_id
        logging.getLogger("apps.request").info(
            "%s %s -> %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "request_id": request_id,
                "duration_ms": round(duration_ms, 2),
                "status": response.status_code,
            },
        )
        if response.status_code >= 500:
            logging.getLogger("apps.error").error(
                "server_error path=%s status=%s",
                request.path,
                response.status_code,
                extra={"request_id": request_id},
            )
        return response
