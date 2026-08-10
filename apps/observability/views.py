"""Accept browser error reports and emit structured JSON ERROR logs.

The frontend is a static Next.js export: it cannot talk to CloudWatch. It POSTs
here; we log at ERROR so the existing metric filter → alarm → triage Lambda →
MiniMax → Jira pipeline picks the event up. Authorization decisions stay on the
server — the client only sends the event.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from config.fingerprint import normalize_path
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from apps.observability.serializers import ClientErrorSerializer

logger = logging.getLogger("apps.observability")


class ClientErrorThrottle(AnonRateThrottle):
    scope = "client_error"


class ClientErrorUserThrottle(UserRateThrottle):
    scope = "client_error_user"


def _client_fingerprint(message: str, url: str, source: str) -> str:
    """Stable key for browser failures (no Python traceback available)."""
    route = normalize_path(url or "/client")
    # Message is included deliberately: client errors lack exception types/frames.
    # Cap length so ids/emails in messages do not explode uniqueness too much.
    parts = ["client", source or "unknown", route, message[:120]]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


class ClientErrorView(APIView):
    """POST /api/observability/client-error/ — public, CSRF-safe when cookie-authed."""

    permission_classes = [AllowAny]
    # Keep default CookieJWTAuthentication: cookie sessions enforce CSRF;
    # anonymous reporters (pre-login) are allowed without a session.
    throttle_classes = [ClientErrorThrottle, ClientErrorUserThrottle]

    def post(self, request: Request) -> Response:
        serializer = ClientErrorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data: dict[str, Any] = serializer.validated_data

        client_url = str(data.get("url") or "")
        message = str(data["message"])
        source = str(data.get("source") or "")
        stack = str(data.get("stack") or "")
        user_agent = str(data.get("user_agent") or "")[:512]
        # Prefer the SPA-supplied id, else the middleware request id on this POST.
        request_id = str(data.get("request_id") or "") or getattr(request, "request_id", "-")
        route = normalize_path(client_url) if client_url else "/client"
        fingerprint = _client_fingerprint(message, client_url, source)
        user = getattr(request, "user", None)
        authenticated = bool(user is not None and getattr(user, "is_authenticated", False))
        user_id = getattr(user, "id", None) if authenticated else None

        # Cap matches serializer / FE MAX_STACK. JsonFormatter promotes ``stack``
        # into the JSON ``exc_info`` field so Logs Insights → triage Lambda can
        # feed the browser stack to MiniMax (Lambda-only; this view never calls
        # an LLM). Production FE bundles are typically minified — React
        # componentStack (source=boundary) is the richest frame data we get.
        capped_stack = stack[:8000] if stack else ""
        stack_hint = (
            "fe_component_stack" if (source or "") == "boundary" else "fe_stack_may_be_minified"
        )
        extra: dict[str, Any] = {
            "request_id": request_id,
            "method": "CLIENT",
            "path": client_url or "-",
            "route": route,
            "status": 0,
            "error_type": "ClientError",
            "fingerprint": fingerprint,
            "user_id": user_id,
            "user_agent": user_agent,
            "source": source or "unknown",
            "stack": capped_stack,
            "stack_hint": stack_hint,
        }

        logger.error(
            "client_error %s at %s [%s]",
            message[:200],
            route,
            stack_hint,
            extra=extra,
        )
        return Response(
            {"detail": "recorded", "fingerprint": fingerprint}, status=status.HTTP_202_ACCEPTED
        )
