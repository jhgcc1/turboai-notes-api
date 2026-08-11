"""Lambda entrypoint for CloudWatch-alarm-driven error triage.

Flow: SNS alarm notification -> sample ERROR logs from CloudWatch Logs Insights
-> group by fingerprint -> deduplicate in DynamoDB -> pull the source code
behind the traceback out of the bundled repo copy -> ask MiniMax to debug it ->
email the report.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from triage import dedup, github_pr, jira, repo
from triage.cloudwatch import build_cloudwatch_url
from triage.llm import analyze
from triage.logs import fetch_error_events, group_by_fingerprint
from triage.notify import build_report, send_report
from triage.settings import Settings, load_secret

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("triage")


def _alarms_from_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract alarm payloads from an SNS envelope or a direct invocation."""
    records = event.get("Records")
    if not records:
        return [event] if event else []

    alarms: list[dict[str, Any]] = []
    for record in records:
        message = (record.get("Sns") or {}).get("Message", "")
        try:
            parsed = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            logger.warning("skipping non-JSON SNS message")
            continue
        if isinstance(parsed, dict):
            alarms.append(parsed)
    return alarms


def should_notify(occurrences: int, is_new: bool, resend_every: int) -> bool:
    """Notify on the first sighting, then only every ``resend_every`` occurrences.

    Without this an alarm that stays in ALARM state would email on every
    evaluation period. The DynamoDB record also carries a TTL, so a bug that
    goes quiet and returns later is treated as new again.
    """
    if is_new:
        return True
    if resend_every <= 0:
        return False
    return occurrences % resend_every == 0


def process_alarm(alarm: dict[str, Any], settings: Settings) -> dict[str, Any]:
    alarm_name = str(alarm.get("AlarmName", "manual-invocation"))
    state = str(alarm.get("NewStateValue", "ALARM"))
    if state != "ALARM":
        # OK / INSUFFICIENT_DATA transitions are status changes, not incidents.
        return {"alarm": alarm_name, "skipped": f"state={state}"}

    events = fetch_error_events(
        settings.log_group,
        settings.lookback_minutes,
        settings.max_events,
    )
    if not events:
        # Infrastructure alarms (ALB, ECS, RDS) can fire with no matching
        # application log line; there is nothing to debug or report.
        return {"alarm": alarm_name, "skipped": "no ERROR log events in window"}

    group = group_by_fingerprint(events)[0]
    record = dedup.register_occurrence(
        settings.dedup_table,
        group.fingerprint,
        settings.dedup_ttl_hours,
    )
    if not should_notify(record.occurrences, record.is_new, settings.resend_every):
        return {
            "alarm": alarm_name,
            "fingerprint": group.fingerprint,
            "action": "suppressed",
            "occurrences": record.occurrences,
        }

    sample = group.representative
    excerpts = repo.collect_context(
        settings.repo_root,
        sample.traceback,
        sample.route,
        max_chars=settings.code_context_chars,
        window_lines=settings.code_window_lines,
    )

    llm_secret = load_secret(settings.llm_secret_id)
    analysis = analyze(
        group,
        api_key=str(llm_secret["api_key"]),
        base_url=str(llm_secret.get("base_url") or settings.llm_base_url),
        model=str(llm_secret.get("model") or settings.llm_model),
        environment=settings.environment,
        alarm_name=alarm_name,
        repo_index=repo.build_index(settings.repo_root),
        code_context=repo.render_context(excerpts),
    )

    cloudwatch_url = build_cloudwatch_url(
        settings.log_group,
        log_stream=sample.log_stream,
        fingerprint=group.fingerprint,
        request_id=sample.request_id,
        lookback_seconds=settings.lookback_minutes * 60,
    )
    # Representative first so Jira metadata (request_id / stream) is richest.
    source_logs = [sample.__dict__] + [
        event.__dict__ for event in group.events if event is not sample
    ]

    issue_key = record.issue_key
    issue_was_created = False
    if issue_key is None and settings.jira_enabled:
        issue_key = jira.create_issue(
            settings,
            analysis,
            source_logs=source_logs,
            alarm_name=alarm_name,
            fingerprint=group.fingerprint,
            cloudwatch_url=cloudwatch_url,
        )
        if issue_key:
            issue_was_created = True
            # Persist the key so the next occurrence of this fingerprint
            # becomes a "comment on existing" rather than a fresh ticket.
            dedup.attach_issue(
                settings.dedup_table,
                group.fingerprint,
                issue_key,
            )
    base_url = settings.jira_base_url or jira.resolve_base_url(settings)
    issue_browse_url = jira.browse_url(base_url, issue_key) if issue_key and base_url else None

    # Tracking branch/PR is best-effort and only runs after a fresh Jira create.
    # Failures must not block email (same pattern as Jira failures).
    if issue_was_created and issue_key:
        tracking = github_pr.open_tracking_branch(
            settings,
            issue_key,
            summary=analysis.summary,
            jira_browse_url=issue_browse_url or "",
            cloudwatch_url=cloudwatch_url,
        )
        if tracking is not None:
            jira.add_comment(settings, issue_key, github_pr.as_comment_text(tracking))

    report = build_report(
        analysis,
        group,
        excerpts,
        environment=settings.environment,
        alarm_name=alarm_name,
        log_group=settings.log_group,
        lookback_minutes=settings.lookback_minutes,
        occurrences=record.occurrences,
        is_recurrence=not record.is_new,
        issue_key=issue_key,
        issue_browse_url=issue_browse_url,
        cloudwatch_url=cloudwatch_url,
    )

    if settings.dry_run:
        logger.info("dry run, report not sent:\n%s", report.body)
        return {
            "alarm": alarm_name,
            "fingerprint": group.fingerprint,
            "action": "dry-run",
            "severity": analysis.severity,
            "subject": report.subject,
            "issue_key": issue_key,
        }

    channel = send_report(
        report,
        topic_arn=settings.sns_topic_arn,
        ses_from=settings.ses_from,
        ses_to=settings.ses_to,
    )
    return {
        "alarm": alarm_name,
        "fingerprint": group.fingerprint,
        "action": "notified",
        "channel": channel,
        "severity": analysis.severity,
        "occurrences": record.occurrences,
        "files_inspected": [excerpt.path for excerpt in excerpts],
        "issue_key": issue_key,
    }


def main(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    settings = Settings.from_env()
    results: list[dict[str, Any]] = []
    for alarm in _alarms_from_event(event):
        try:
            result = process_alarm(alarm, settings)
        except Exception as exc:  # noqa: BLE001 - one bad alarm must not drop the rest
            logger.exception("triage failed for alarm")
            result = {"alarm": alarm.get("AlarmName", "?"), "error": f"{type(exc).__name__}: {exc}"}
        logger.info("triage result: %s", json.dumps(result, default=str))
        results.append(result)
    return {"processed": len(results), "results": results}
