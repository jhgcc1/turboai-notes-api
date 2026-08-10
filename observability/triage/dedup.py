"""Fingerprint bookkeeping so a recurring bug does not email on every alarm."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DedupRecord:
    is_new: bool
    occurrences: int
    issue_key: str | None


def _client(client: Any) -> Any:
    if client is not None:
        return client
    import boto3  # pragma: no cover - real AWS path

    return boto3.client("dynamodb")  # pragma: no cover - real AWS path


def register_occurrence(
    table: str,
    fingerprint: str,
    ttl_hours: int,
    *,
    client: Any = None,
) -> DedupRecord:
    """Atomically count this occurrence and report whether it is the first.

    A single conditional-free ``UpdateItem`` with ``ALL_OLD`` avoids the
    read-then-write race that would let two concurrent alarm deliveries both
    decide they are first and open duplicate tickets.
    """
    ddb = _client(client)
    now = int(time.time())
    response = ddb.update_item(
        TableName=table,
        Key={"fingerprint": {"S": fingerprint}},
        UpdateExpression=(
            "ADD #occurrences :one "
            "SET #first_seen = if_not_exists(#first_seen, :now), "
            "#last_seen = :now, #expires_at = :ttl"
        ),
        ExpressionAttributeNames={
            "#occurrences": "occurrences",
            "#first_seen": "first_seen",
            "#last_seen": "last_seen",
            "#expires_at": "expires_at",
        },
        ExpressionAttributeValues={
            ":one": {"N": "1"},
            ":now": {"N": str(now)},
            ":ttl": {"N": str(now + ttl_hours * 3600)},
        },
        ReturnValues="ALL_OLD",
    )
    previous = response.get("Attributes") or {}
    previous_count = int(previous.get("occurrences", {}).get("N", "0"))
    issue_key = previous.get("issue_key", {}).get("S")
    return DedupRecord(
        is_new=not previous,
        occurrences=previous_count + 1,
        issue_key=issue_key,
    )


def attach_issue(table: str, fingerprint: str, issue_key: str, *, client: Any = None) -> None:
    """Record the ticket a fingerprint resolved to, for later recurrences."""
    ddb = _client(client)
    ddb.update_item(
        TableName=table,
        Key={"fingerprint": {"S": fingerprint}},
        UpdateExpression="SET #issue_key = :issue_key",
        ExpressionAttributeNames={"#issue_key": "issue_key"},
        ExpressionAttributeValues={":issue_key": {"S": issue_key}},
    )
