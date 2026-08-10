"""Log grouping, deduplication, notification and handler orchestration."""

from __future__ import annotations

import json
from typing import Any

import handler as handler_module
import pytest
from triage import dedup, notify
from triage.llm import Analysis
from triage.logs import ErrorGroup, LogEvent, fetch_error_events, group_by_fingerprint
from triage.settings import ConfigError, Settings


def _event(fingerprint: str, message: str = "boom", traceback: str = "") -> LogEvent:
    return LogEvent(
        timestamp="2026-08-10T12:00:00Z",
        message=message,
        logger="apps.error",
        route="/api/notes/{id}/",
        method="GET",
        status="500",
        error_type="ValueError",
        fingerprint=fingerprint,
        request_id="req-1",
        traceback=traceback,
    )


class FakeLogsClient:
    def __init__(self, rows: list[list[dict[str, str]]], statuses: list[str]) -> None:
        self.rows = rows
        self.statuses = statuses
        self.stopped = False

    def start_query(self, **kwargs: Any) -> dict[str, str]:
        self.query = kwargs
        return {"queryId": "q-1"}

    def get_query_results(self, queryId: str) -> dict[str, Any]:
        status = self.statuses.pop(0)
        return {"status": status, "results": self.rows if status == "Complete" else []}

    def stop_query(self, queryId: str) -> None:
        self.stopped = True


def test_fetch_error_events_parses_rows() -> None:
    rows = [
        [
            {"field": "@timestamp", "value": "2026-08-10 12:00:00"},
            {"field": "message", "value": "[ERROR] boom"},
            {"field": "fingerprint", "value": "abc"},
            {"field": "exc_info", "value": "Traceback"},
        ]
    ]
    client = FakeLogsClient(rows, ["Complete"])
    events = fetch_error_events("lg", 15, 10, client=client, poll_interval=0)
    assert len(events) == 1
    assert events[0].fingerprint == "abc"
    assert events[0].traceback == "Traceback"


def test_fetch_error_events_falls_back_to_stack_field() -> None:
    """FE client-error logs may only have ``stack`` if formatter promotion lags."""
    rows = [
        [
            {"field": "message", "value": "[ERROR] client_error"},
            {"field": "fingerprint", "value": "fe1"},
            {"field": "stack", "value": "Error: x\n    at app.js:1"},
            {"field": "source", "value": "window.onerror"},
            {"field": "stack_hint", "value": "fe_stack_may_be_minified"},
        ]
    ]
    client = FakeLogsClient(rows, ["Complete"])
    events = fetch_error_events("lg", 15, 10, client=client, poll_interval=0)
    assert events[0].traceback.startswith("Error: x")
    assert events[0].source == "window.onerror"
    assert events[0].stack_hint == "fe_stack_may_be_minified"


def test_fetch_error_events_polls_until_complete() -> None:
    client = FakeLogsClient([], ["Running", "Complete"])
    assert fetch_error_events("lg", 15, 10, client=client, poll_interval=0) == []


def test_fetch_error_events_returns_empty_on_failure() -> None:
    client = FakeLogsClient([], ["Failed"])
    assert fetch_error_events("lg", 15, 10, client=client, poll_interval=0) == []


def test_fetch_error_events_stops_on_timeout() -> None:
    client = FakeLogsClient([], ["Running"] * 5)
    result = fetch_error_events("lg", 15, 10, client=client, poll_interval=0, max_wait_seconds=-1)
    assert result == []
    assert client.stopped is True


def test_group_by_fingerprint_orders_by_frequency() -> None:
    events = [_event("a"), _event("b"), _event("a")]
    groups = group_by_fingerprint(events)
    assert groups[0].fingerprint == "a"
    assert groups[0].count == 2


def test_group_prefers_event_with_traceback() -> None:
    group = ErrorGroup("a", [_event("a"), _event("a", traceback="Traceback...")])
    assert group.representative.traceback


def test_group_falls_back_when_fingerprint_missing() -> None:
    groups = group_by_fingerprint([_event("")])
    assert groups[0].fingerprint == "ValueError:/api/notes/{id}/"


class FakeDynamo:
    def __init__(self, previous: dict[str, Any] | None = None) -> None:
        self.previous = previous
        self.calls: list[dict[str, Any]] = []

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"Attributes": self.previous} if self.previous else {}


def test_register_occurrence_reports_first_sighting() -> None:
    client = FakeDynamo()
    record = dedup.register_occurrence("t", "fp", 72, client=client)
    assert record.is_new is True
    assert record.occurrences == 1


def test_register_occurrence_counts_recurrence() -> None:
    client = FakeDynamo({"occurrences": {"N": "4"}})
    record = dedup.register_occurrence("t", "fp", 72, client=client)
    assert record.is_new is False
    assert record.occurrences == 5


def test_attach_issue_writes_key() -> None:
    client = FakeDynamo()
    dedup.attach_issue("t", "fp", "OPS-1", client=client)
    assert client.calls[0]["ExpressionAttributeValues"][":issue_key"]["S"] == "OPS-1"


def _analysis(**overrides: Any) -> Analysis:
    defaults: dict[str, Any] = {
        "severity": "high",
        "summary": "Boom in notes",
        "root_cause": "Because of X.",
        "suspected_locations": ["apps/notes/views.py:10"],
        "proposed_fix": "--- a\n+++ b",
        "fix_steps": ["do this"],
        "repro_steps": ["call endpoint"],
        "confidence": "high",
        "model": "MiniMax-M2.1",
    }
    defaults.update(overrides)
    return Analysis(**defaults)


def test_build_report_contains_required_sections() -> None:
    group = ErrorGroup("fp1", [_event("fp1", traceback="Traceback...")])
    report = notify.build_report(
        _analysis(),
        group,
        [],
        environment="staging",
        alarm_name="alarm-1",
        log_group="/turboai/notes/staging/api",
        lookback_minutes=15,
        occurrences=3,
        is_recurrence=False,
    )
    for expected in ("ROOT CAUSE", "PROPOSED FIX", "REPRODUCTION", "fp1", "staging", "alarm-1"):
        assert expected in report.body
    assert report.subject.startswith("[staging] NEW HIGH")
    assert len(report.subject) <= 100


def test_build_report_marks_recurrence_and_degradation() -> None:
    group = ErrorGroup("fp1", [_event("fp1")])
    report = notify.build_report(
        _analysis(degraded=True),
        group,
        [],
        environment="prod",
        alarm_name="a",
        log_group="lg",
        lookback_minutes=15,
        occurrences=20,
        is_recurrence=True,
    )
    assert "RECURRING" in report.subject
    assert "LLM call failed" in report.body or "raw log data only" in report.body


class FakeSns:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


class FakeSes:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict[str, Any]] = []

    def send_email(self, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("identity not verified")
        self.sent.append(kwargs)


def _report() -> notify.Report:
    return notify.Report(subject="subject", body="body")


def test_send_report_uses_sns_by_default() -> None:
    sns = FakeSns()
    channel = notify.send_report(_report(), topic_arn="arn", sns_client=sns)
    assert channel == "sns"
    assert sns.published[0]["TopicArn"] == "arn"


def test_send_report_prefers_ses_when_configured() -> None:
    ses, sns = FakeSes(), FakeSns()
    channel = notify.send_report(
        _report(),
        topic_arn="arn",
        ses_from="ops@example.com",
        ses_to="dev@example.com",
        ses_client=ses,
        sns_client=sns,
    )
    assert channel == "ses"
    assert not sns.published


def test_send_report_falls_back_to_sns_when_ses_fails() -> None:
    ses, sns = FakeSes(fail=True), FakeSns()
    channel = notify.send_report(
        _report(),
        topic_arn="arn",
        ses_from="ops@example.com",
        ses_to="dev@example.com",
        ses_client=ses,
        sns_client=sns,
    )
    assert channel == "sns"
    assert sns.published


@pytest.mark.parametrize(
    ("occurrences", "is_new", "resend_every", "expected"),
    [
        (1, True, 10, True),
        (2, False, 10, False),
        (10, False, 10, True),
        (20, False, 10, True),
        (5, False, 0, False),
    ],
)
def test_should_notify_policy(
    occurrences: int, is_new: bool, resend_every: int, expected: bool
) -> None:
    assert handler_module.should_notify(occurrences, is_new, resend_every) is expected


def test_alarms_from_sns_envelope() -> None:
    event = {"Records": [{"Sns": {"Message": json.dumps({"AlarmName": "a"})}}]}
    assert handler_module._alarms_from_event(event) == [{"AlarmName": "a"}]


def test_alarms_from_direct_invocation() -> None:
    assert handler_module._alarms_from_event({"AlarmName": "manual"}) == [{"AlarmName": "manual"}]
    assert handler_module._alarms_from_event({}) == []


def test_alarms_skips_non_json_message() -> None:
    assert handler_module._alarms_from_event({"Records": [{"Sns": {"Message": "oops"}}]}) == []


def _settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "log_group": "lg",
        "environment": "staging",
        "dedup_table": "table",
        "llm_secret_id": "secret",
        "llm_base_url": "https://api.minimax.io/v1",
        "llm_model": "MiniMax-M2.1",
        "sns_topic_arn": "arn:reports",
        "ses_from": "",
        "ses_to": "",
        "repo_root": "/nonexistent",
        "lookback_minutes": 15,
        "max_events": 25,
        "dedup_ttl_hours": 72,
        "resend_every": 10,
        "code_context_chars": 24000,
        "code_window_lines": 40,
        "dry_run": False,
        "jira_enabled": False,
        "jira_base_url": "",
        "jira_project_key": "",
        "jira_issue_type": "Bug",
        "jira_secret_id": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_process_alarm_ignores_ok_transition() -> None:
    result = handler_module.process_alarm({"AlarmName": "a", "NewStateValue": "OK"}, _settings())
    assert "skipped" in result


def test_process_alarm_skips_when_no_events(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler_module, "fetch_error_events", lambda *a, **k: [])
    result = handler_module.process_alarm({"AlarmName": "a"}, _settings())
    assert result["skipped"] == "no ERROR log events in window"


def test_process_alarm_sends_report(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    monkeypatch.setattr(
        handler_module, "fetch_error_events", lambda *a, **k: [_event("fp1", traceback="T")]
    )
    monkeypatch.setattr(
        handler_module.dedup,
        "register_occurrence",
        lambda *a, **k: dedup.DedupRecord(is_new=True, occurrences=1, issue_key=None),
    )
    monkeypatch.setattr(handler_module, "load_secret", lambda _id: {"api_key": "k"})
    monkeypatch.setattr(handler_module, "analyze", lambda *a, **k: _analysis())

    def fake_send(report: notify.Report, **kwargs: Any) -> str:
        sent["subject"] = report.subject
        return "sns"

    monkeypatch.setattr(handler_module, "send_report", fake_send)
    result = handler_module.process_alarm({"AlarmName": "a"}, _settings())
    assert result["action"] == "notified"
    assert result["channel"] == "sns"
    assert sent["subject"]


def test_process_alarm_suppresses_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler_module, "fetch_error_events", lambda *a, **k: [_event("fp1")])
    monkeypatch.setattr(
        handler_module.dedup,
        "register_occurrence",
        lambda *a, **k: dedup.DedupRecord(is_new=False, occurrences=3, issue_key=None),
    )
    result = handler_module.process_alarm({"AlarmName": "a"}, _settings())
    assert result["action"] == "suppressed"


def test_process_alarm_creates_jira_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler_module, "fetch_error_events", lambda *a, **k: [_event("fp1")])
    monkeypatch.setattr(
        handler_module.dedup,
        "register_occurrence",
        lambda *a, **k: dedup.DedupRecord(is_new=True, occurrences=1, issue_key=None),
    )
    monkeypatch.setattr(handler_module, "load_secret", lambda _id: {"api_key": "k"})
    monkeypatch.setattr(handler_module, "analyze", lambda *a, **k: _analysis())
    monkeypatch.setattr(handler_module.jira, "create_issue", lambda *a, **k: "OPS-7")
    attached: list[tuple[str, str]] = []
    monkeypatch.setattr(
        handler_module.dedup,
        "attach_issue",
        lambda table, fp, key, **kwargs: attached.append((table, fp, key)),
    )
    sent: dict[str, Any] = {}

    def fake_send(report: notify.Report, **kwargs: Any) -> str:
        sent["subject"] = report.subject
        sent["issue_key"] = report.issue_key
        sent["issue_browse_url"] = report.issue_browse_url
        return "sns"

    monkeypatch.setattr(handler_module, "send_report", fake_send)
    settings = _settings(
        jira_enabled=True,
        jira_base_url="https://acme.atlassian.net",
        jira_project_key="OPS",
        jira_issue_type="Bug",
        jira_secret_id="secret-id",
    )
    result = handler_module.process_alarm({"AlarmName": "a"}, settings)
    assert result["action"] == "notified"
    assert result["issue_key"] == "OPS-7"
    assert attached == [(settings.dedup_table, "fp1", "OPS-7")]
    assert "[OPS-7]" in sent["subject"]
    assert sent["issue_browse_url"] == "https://acme.atlassian.net/browse/OPS-7"


def test_process_alarm_uses_existing_issue_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the dedup record already has a key, no new Jira call is made."""
    monkeypatch.setattr(handler_module, "fetch_error_events", lambda *a, **k: [_event("fp1")])
    monkeypatch.setattr(
        handler_module.dedup,
        "register_occurrence",
        lambda *a, **k: dedup.DedupRecord(is_new=False, occurrences=10, issue_key="OPS-1"),
    )
    monkeypatch.setattr(handler_module, "load_secret", lambda _id: {"api_key": "k"})
    monkeypatch.setattr(handler_module, "analyze", lambda *a, **k: _analysis())

    def must_not_run(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("Jira create_issue must not be called when an existing key is present")

    monkeypatch.setattr(handler_module.jira, "create_issue", must_not_run)
    sent: dict[str, Any] = {}

    def fake_send(report: notify.Report, **kwargs: Any) -> str:
        sent["issue_key"] = report.issue_key
        return "sns"

    monkeypatch.setattr(handler_module, "send_report", fake_send)
    settings = _settings(
        jira_enabled=True,
        jira_base_url="https://acme.atlassian.net",
        jira_project_key="OPS",
        jira_issue_type="Bug",
        jira_secret_id="secret-id",
    )
    result = handler_module.process_alarm({"AlarmName": "a"}, settings)
    assert result["action"] == "notified"
    assert sent["issue_key"] == "OPS-1"
    assert result["issue_key"] == "OPS-1"


def test_process_alarm_continues_when_jira_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jira failures must not block the email."""
    monkeypatch.setattr(handler_module, "fetch_error_events", lambda *a, **k: [_event("fp1")])
    monkeypatch.setattr(
        handler_module.dedup,
        "register_occurrence",
        lambda *a, **k: dedup.DedupRecord(is_new=True, occurrences=1, issue_key=None),
    )
    monkeypatch.setattr(handler_module, "load_secret", lambda _id: {"api_key": "k"})
    monkeypatch.setattr(handler_module, "analyze", lambda *a, **k: _analysis())
    monkeypatch.setattr(handler_module.jira, "create_issue", lambda *a, **k: None)
    sent: dict[str, Any] = {}

    def fake_send(report: notify.Report, **kwargs: Any) -> str:
        sent["subject"] = report.subject
        sent["issue_key"] = report.issue_key
        return "sns"

    monkeypatch.setattr(handler_module, "send_report", fake_send)
    settings = _settings(
        jira_enabled=True,
        jira_base_url="https://acme.atlassian.net",
        jira_project_key="OPS",
        jira_issue_type="Bug",
        jira_secret_id="secret-id",
    )
    result = handler_module.process_alarm({"AlarmName": "a"}, settings)
    assert result["action"] == "notified"
    assert result["issue_key"] is None
    assert sent["issue_key"] is None


def test_process_alarm_dry_run_skips_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler_module, "fetch_error_events", lambda *a, **k: [_event("fp1")])
    monkeypatch.setattr(
        handler_module.dedup,
        "register_occurrence",
        lambda *a, **k: dedup.DedupRecord(is_new=True, occurrences=1, issue_key=None),
    )
    monkeypatch.setattr(handler_module, "load_secret", lambda _id: {"api_key": "k"})
    monkeypatch.setattr(handler_module, "analyze", lambda *a, **k: _analysis())

    def explode(*args: Any, **kwargs: Any) -> str:  # pragma: no cover - must not run
        raise AssertionError("dry run must not send")

    monkeypatch.setattr(handler_module, "send_report", explode)
    result = handler_module.process_alarm({"AlarmName": "a"}, _settings(dry_run=True))
    assert result["action"] == "dry-run"


def test_main_isolates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: _settings()))

    def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("nope")

    monkeypatch.setattr(handler_module, "process_alarm", boom)
    result = handler_module.main({"AlarmName": "a"})
    assert result["processed"] == 1
    assert "RuntimeError" in result["results"][0]["error"]


def test_settings_requires_core_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CW_LOG_GROUP", "DEDUP_TABLE", "LLM_SECRET_ID", "SNS_TOPIC_ARN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()
