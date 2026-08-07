"""CloudWatch Logs helpers for Turbo Notes App Runner services."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import config
from tools import aws_session


def list_groups(prefix: str | None = None, limit: int = 50) -> dict[str, Any]:
    client = aws_session.get_client("logs")
    kwargs: dict[str, Any] = {"limit": min(limit, 50)}
    if prefix or config.LOG_GROUP_PREFIX:
        kwargs["logGroupNamePrefix"] = prefix or config.LOG_GROUP_PREFIX
    resp = client.describe_log_groups(**kwargs)
    groups = [g["logGroupName"] for g in resp.get("logGroups", [])]
    return {"env_label": "SHARED", "groups": groups}


def tail(
    log_group: str,
    minutes: int = 15,
    filter_pattern: str | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    client = aws_session.get_client("logs")
    start = int((datetime.now(timezone.utc) - timedelta(minutes=minutes)).timestamp() * 1000)
    kwargs: dict[str, Any] = {
        "logGroupName": log_group,
        "startTime": start,
        "limit": max_events or config.LOGS_MAX_EVENTS,
        "interleaved": True,
    }
    if filter_pattern:
        kwargs["filterPattern"] = filter_pattern
    resp = client.filter_log_events(**kwargs)
    events = [
        {
            "timestamp": e.get("timestamp"),
            "message": e.get("message"),
            "stream": e.get("logStreamName"),
        }
        for e in resp.get("events", [])
    ]
    return {"env_label": "SHARED", "log_group": log_group, "events": events}


def insights_query(
    log_groups: list[str] | str,
    query: str,
    minutes: int = 60,
    timeout: int = 30,
) -> dict[str, Any]:
    client = aws_session.get_client("logs")
    groups = [log_groups] if isinstance(log_groups, str) else list(log_groups)
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    qid = client.start_query(
        logGroupNames=groups,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
    )["queryId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = client.get_query_results(queryId=qid)
        if result["status"] in ("Complete", "Failed", "Cancelled", "Timeout"):
            return {
                "env_label": "SHARED",
                "status": result["status"],
                "results": result.get("results", []),
            }
        time.sleep(1)
    return {"env_label": "SHARED", "status": "Timeout", "query_id": qid}
