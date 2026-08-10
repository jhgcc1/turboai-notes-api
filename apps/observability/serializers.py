"""Client-error intake — validate payload size; never trust the client for auth."""

from __future__ import annotations

from rest_framework import serializers

# Hard caps so a malicious or buggy client cannot flood log/LLM cost budgets.
_MAX_MESSAGE = 2_000
_MAX_STACK = 8_000
_MAX_URL = 2_000
_MAX_UA = 512
_MAX_REQUEST_ID = 64


class ClientErrorSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=_MAX_MESSAGE, allow_blank=False)
    stack = serializers.CharField(
        max_length=_MAX_STACK, required=False, allow_blank=True, default=""
    )
    url = serializers.CharField(max_length=_MAX_URL, required=False, allow_blank=True, default="")
    user_agent = serializers.CharField(required=False, allow_blank=True, default="")
    # Optional correlation id the SPA may have seen on a prior API response.
    request_id = serializers.CharField(
        max_length=_MAX_REQUEST_ID, required=False, allow_blank=True, default=""
    )
    # Free-form source tag: "window.onerror" | "unhandledrejection" | "boundary"
    source = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")

    def validate_user_agent(self, value: str) -> str:
        return (value or "")[:_MAX_UA]
