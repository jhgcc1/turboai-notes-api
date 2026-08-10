"""Collect the error logs that explain why an alarm fired."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Only ERROR rows: WARNING carries client-side 4xx noise that would dilute the
# sample the LLM reasons over.
INSIGHTS_QUERY = """
fields @timestamp, @logStream, level, logger, message, request_id, route,
       method, status, error_type, fingerprint, exc_info
| filter level = "ERROR"
| sort @timestamp desc
| limit {limit}
"""

_TERMINAL_STATUSES = frozenset({"Complete", "Failed", "Cancelled", "Timeout", "Unknown"})


@dataclass(frozen=True)
class LogEvent:
    timestamp: str
    message: str
    logger: str
    route: str
    method: str
    status: str
    error_type: str
    fingerprint: str
    request_id: str
    traceback: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> LogEvent:
        return cls(
            timestamp=row.get("@timestamp", ""),
            message=row.get("message", ""),
            logger=row.get("logger", ""),
            route=row.get("route", ""),
            method=row.get("method", ""),
            status=row.get("status", ""),
            error_type=row.get("error_type", ""),
            fingerprint=row.get("fingerprint", ""),
            request_id=row.get("request_id", ""),
            traceback=row.get("exc_info", ""),
        )


@dataclass
class ErrorGroup:
    """All sampled occurrences that share one fingerprint."""

    fingerprint: str
    events: list[LogEvent] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def representative(self) -> LogEvent:
        # Prefer an occurrence that carries a traceback; a bare message gives
        # the LLM almost nothing to work with.
        for event in self.events:
            if event.traceback:
                return event
        return self.events[0]


def fetch_error_events(
    log_group: str,
    lookback_minutes: int,
    limit: int,
    *,
    client: Any = None,
    poll_interval: float = 1.0,
    max_wait_seconds: float = 45.0,
) -> list[LogEvent]:
    """Run a Logs Insights query and return the matching rows."""
    if client is None:  # pragma: no cover - exercised via injected fake in tests
        import boto3

        client = boto3.client("logs")

    end = int(time.time())
    start = end - lookback_minutes * 60
    started = client.start_query(
        logGroupName=log_group,
        startTime=start,
        endTime=end,
        queryString=INSIGHTS_QUERY.format(limit=limit),
        limit=limit,
    )
    query_id = started["queryId"]

    deadline = time.monotonic() + max_wait_seconds
    while True:
        result = client.get_query_results(queryId=query_id)
        status = result.get("status", "Unknown")
        if status in _TERMINAL_STATUSES:
            break
        if time.monotonic() >= deadline:
            client.stop_query(queryId=query_id)
            status = "Timeout"
            result = {"results": []}
            break
        time.sleep(poll_interval)

    if status != "Complete":
        return []

    events: list[LogEvent] = []
    for row in result.get("results", []):
        fields = {item["field"]: item["value"] for item in row if "field" in item}
        events.append(LogEvent.from_row(fields))
    return events


def group_by_fingerprint(events: list[LogEvent]) -> list[ErrorGroup]:
    """Group events, most frequent first, so triage targets the loudest bug."""
    groups: dict[str, ErrorGroup] = {}
    for event in events:
        # Pre-fingerprint log lines (or non-DRF errors) still need a key;
        # error type plus route is the closest stable stand-in.
        key = event.fingerprint or f"{event.error_type}:{event.route}" or "unknown"
        groups.setdefault(key, ErrorGroup(fingerprint=key)).events.append(event)
    return sorted(groups.values(), key=lambda group: group.count, reverse=True)
