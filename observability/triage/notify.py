"""Deliver the triage report by email.

SNS is the primary channel: a topic subscription needs no domain, no identity
verification and no extra cost, which matters for a demo account. SES is used
only when explicitly configured, and any SES failure falls back to SNS so a
report is never lost to a delivery misconfiguration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from triage.llm import Analysis
from triage.logs import ErrorGroup
from triage.repo import CodeExcerpt

logger = logging.getLogger("triage.notify")

# SNS subject: ASCII, single line, hard limit of 100 characters.
_SNS_SUBJECT_LIMIT = 100
_LOG_SNIPPET_LIMIT = 2000
_TRACEBACK_LIMIT = 3000


@dataclass(frozen=True)
class Report:
    subject: str
    body: str
    issue_key: str | None = None
    issue_browse_url: str | None = None


def _bullets(items: list[str], empty: str = "  (none provided)") -> str:
    return "\n".join(f"  - {item}" for item in items) if items else empty


def build_report(
    analysis: Analysis,
    group: ErrorGroup,
    excerpts: list[CodeExcerpt],
    *,
    environment: str,
    alarm_name: str,
    log_group: str,
    lookback_minutes: int,
    occurrences: int,
    is_recurrence: bool,
    issue_key: str | None = None,
    issue_browse_url: str | None = None,
    cloudwatch_url: str = "",
) -> Report:
    sample = group.representative
    prefix = "RECURRING" if is_recurrence else "NEW"
    # When the fingerprint already has a Jira ticket we say so in the
    # subject itself; new issues get the key appended after creation.
    subject_source = f"[{environment}] {prefix} {analysis.severity.upper()}: {analysis.summary}"
    if issue_key:
        subject_source = f"[{issue_key}] {subject_source}"
    subject = subject_source[:_SNS_SUBJECT_LIMIT]

    files_inspected = (
        ", ".join(f"{excerpt.path}:{excerpt.start_line}-{excerpt.end_line}" for excerpt in excerpts)
        or "(none resolved)"
    )

    ticket_line = (
        f"Jira            : {issue_browse_url or issue_key}"
        if issue_key
        else "Jira            : (not created)"
    )

    sections = [
        "TURBO AI NOTES - AUTOMATED ERROR TRIAGE",
        "=" * 60,
        "",
        f"Environment      : {environment}",
        f"Alarm            : {alarm_name}",
        f"Severity         : {analysis.severity.upper()} (model confidence: {analysis.confidence})",
        f"Occurrences      : {occurrences} total, {group.count} in the last "
        f"{lookback_minutes} minutes",
        f"Fingerprint      : {group.fingerprint}",
        f"Endpoint         : {sample.method} {sample.route} -> {sample.status}",
        f"Error type       : {sample.error_type or '(unknown)'}",
        f"Log group        : {log_group}",
        f"Log stream       : {sample.log_stream or '(none)'}",
        f"Sample request id: {sample.request_id or '(none)'}",
        f"Analysed by      : {analysis.model or '(no model)'}",
        ticket_line,
        (
            f"CloudWatch       : {cloudwatch_url}"
            if cloudwatch_url
            else "CloudWatch       : (not available)"
        ),
        "",
        "-" * 60,
        "ROOT CAUSE",
        "-" * 60,
        analysis.root_cause,
        "",
        "-" * 60,
        "SUSPECTED LOCATIONS",
        "-" * 60,
        _bullets(analysis.suspected_locations),
        "",
        "-" * 60,
        "PROPOSED FIX",
        "-" * 60,
        analysis.proposed_fix or "(no patch proposed)",
        "",
        "Steps:",
        _bullets(analysis.fix_steps),
        "",
        "-" * 60,
        "REPRODUCTION",
        "-" * 60,
        _bullets(analysis.repro_steps),
        "",
        "-" * 60,
        "SOURCE FILES INSPECTED",
        "-" * 60,
        files_inspected,
        "",
        "-" * 60,
        "ORIGINAL LOG EVENT",
        "-" * 60,
        (sample.message or "(no message)")[:_LOG_SNIPPET_LIMIT],
        "",
        "Traceback:",
        (sample.traceback or "(none captured)")[:_TRACEBACK_LIMIT],
        "",
        "=" * 60,
        "Filed automatically by the turboai-notes error-triage Lambda.",
        "The analysis above is LLM-generated - verify before acting on it.",
    ]
    if analysis.degraded:
        sections.insert(
            2,
            "NOTE: the LLM call failed or returned an unusable response; "
            "this report contains raw log data only.\n",
        )
    return Report(
        subject=subject,
        body="\n".join(sections),
        issue_key=issue_key,
        issue_browse_url=issue_browse_url,
    )


def _send_ses(report: Report, sender: str, recipients: list[str], client: Any) -> bool:
    if client is None:
        import boto3

        client = boto3.client("ses")
    client.send_email(
        Source=sender,
        Destination={"ToAddresses": recipients},
        Message={
            "Subject": {"Data": report.subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": report.body, "Charset": "UTF-8"}},
        },
    )
    return True


def _send_sns(report: Report, topic_arn: str, client: Any) -> None:
    if client is None:
        import boto3

        client = boto3.client("sns")
    client.publish(TopicArn=topic_arn, Subject=report.subject, Message=report.body)


def send_report(
    report: Report,
    *,
    topic_arn: str,
    ses_from: str = "",
    ses_to: str = "",
    ses_client: Any = None,
    sns_client: Any = None,
) -> str:
    """Send via SES when configured, otherwise SNS. Returns the channel used."""
    recipients = [address.strip() for address in ses_to.split(",") if address.strip()]
    if ses_from and recipients:
        try:
            _send_ses(report, ses_from, recipients, ses_client)
        except Exception:  # noqa: BLE001 - unverified identity, sandbox, throttling
            logger.exception("SES delivery failed; falling back to SNS")
        else:
            return "ses"

    _send_sns(report, topic_arn, sns_client)
    return "sns"
