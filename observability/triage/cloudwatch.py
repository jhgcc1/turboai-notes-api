"""Build CloudWatch console deep links for triage reports / Jira.

The AWS console uses a distinctive fragment encoding: URI-encode once or twice,
then replace ``%`` with ``$`` (so ``/`` becomes ``$252F`` after double-encoding).
Links are region-scoped; Lambda sets ``AWS_REGION`` automatically.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote


def resolve_region(explicit: str = "") -> str:
    if explicit.strip():
        return explicit.strip()
    return (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-2").strip()


def console_encode(value: str) -> str:
    """Double-encode a path segment for CloudWatch console URL fragments."""
    return quote(quote(value, safe=""), safe="").replace("%", "$")


def star_encode(value: str) -> str:
    """Percent-encode with ``*`` instead of ``%`` (Logs Insights queryDetail)."""
    return quote(value, safe="").replace("%", "*")


def build_log_group_url(log_group: str, *, region: str = "") -> str:
    region = resolve_region(region)
    encoded = console_encode(log_group)
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#logsV2:log-groups/log-group/{encoded}"
    )


def build_log_stream_url(log_group: str, log_stream: str, *, region: str = "") -> str:
    region = resolve_region(region)
    return (
        f"{build_log_group_url(log_group, region=region)}/log-events/{console_encode(log_stream)}"
    )


def build_insights_url(
    log_group: str,
    *,
    region: str = "",
    fingerprint: str = "",
    request_id: str = "",
    lookback_seconds: int = 900,
) -> str:
    """Logs Insights deep link filtered by fingerprint and/or request_id."""
    region = resolve_region(region)
    clauses: list[str] = ['level = "ERROR"']
    if fingerprint:
        clauses.append(f'fingerprint = "{fingerprint}"')
    if request_id:
        clauses.append(f'request_id = "{request_id}"')
    query = (
        "fields @timestamp, @logStream, @message, request_id, fingerprint, "
        "error_type, route, exc_info\n"
        f"| filter {' and '.join(clauses)}\n"
        "| sort @timestamp desc\n"
        "| limit 20"
    )
    editor = star_encode(query)
    # Insights ``source`` entries use single $2F-style encoding of the group name.
    source = quote(log_group, safe="").replace("%", "$")
    detail = (
        f"(end~0~start~-{max(60, lookback_seconds)}~timeType~'RELATIVE~tz~'UTC"
        f"~editorString~'{editor}"
        f"~source~(~'{source}))"
    )
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
        f"#logsV2:logs-insights$3FqueryDetail$3D{detail}"
    )


def build_cloudwatch_url(
    log_group: str,
    *,
    region: str = "",
    log_stream: str = "",
    fingerprint: str = "",
    request_id: str = "",
    lookback_seconds: int = 900,
) -> str:
    """Prefer Insights (filterable) when fingerprint/request_id exist; else stream/group."""
    if fingerprint or request_id:
        return build_insights_url(
            log_group,
            region=region,
            fingerprint=fingerprint,
            request_id=request_id,
            lookback_seconds=lookback_seconds,
        )
    if log_stream:
        return build_log_stream_url(log_group, log_stream, region=region)
    return build_log_group_url(log_group, region=region)


def url_from_log_events(
    log_group: str,
    source_logs: list[dict[str, Any]] | None,
    *,
    region: str = "",
    lookback_minutes: int = 15,
) -> str:
    """Pick identifiers from the first sampled event and build the best URL."""
    sample = source_logs[0] if source_logs else {}
    return build_cloudwatch_url(
        log_group,
        region=region,
        log_stream=str(sample.get("log_stream") or ""),
        fingerprint=str(sample.get("fingerprint") or ""),
        request_id=str(sample.get("request_id") or ""),
        lookback_seconds=max(60, lookback_minutes * 60),
    )
