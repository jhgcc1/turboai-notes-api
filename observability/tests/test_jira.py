"""Jira integration tests.

The tests must not need network or ``boto3``: ``create_issue`` is the
production seam, everything around it (Secrets Manager, urllib) is
injected. ``Settings`` is built by hand to keep the file independent
from ``os.environ`` mutations.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from triage import jira
from triage.llm import Analysis
from triage.settings import Settings


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
        "jira_enabled": True,
        "jira_base_url": "https://acme.atlassian.net",
        "jira_project_key": "OPS",
        "jira_issue_type": "Bug",
        "jira_secret_id": "secret-id",
    }
    defaults.update(overrides)
    return Settings(**defaults)


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


def test_create_issue_returns_none_when_disabled() -> None:
    assert jira.create_issue(_settings(jira_enabled=False), _analysis()) is None


def test_create_issue_returns_none_in_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jira,
        "load_secret",
        lambda _id: {
            "base_url": "https://acme.atlassian.net",
            "email": "ops@example.com",
            "api_token": "tok",
        },
    )
    assert jira.create_issue(_settings(dry_run=True), _analysis()) is None


def test_create_issue_returns_none_when_project_key_empty() -> None:
    settings = _settings(jira_project_key="")
    assert jira.create_issue(settings, _analysis()) is None


def test_create_issue_handles_secret_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jira,
        "load_secret",
        lambda _id: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert jira.create_issue(_settings(), _analysis()) is None


def test_create_issue_handles_malformed_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jira, "load_secret", lambda _id: {"base_url": "x"})  # missing email/token
    assert jira.create_issue(_settings(), _analysis()) is None


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_create_issue_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = {
        "base_url": "https://acme.atlassian.net",
        "email": "ops@example.com",
        "api_token": "tok",
    }
    monkeypatch.setattr(jira, "load_secret", lambda _id: secret_value)

    response = {
        "key": "OPS-42",
        "id": "12345",
        "self": "https://acme.atlassian.net/rest/api/3/issue/12345",
    }
    captured: list[Any] = []

    def fake_urlopen(request: Any, timeout: int = 10) -> Any:
        captured.append((request, timeout))
        return _FakeResponse(response)

    monkeypatch.setattr(jira.urllib.request, "urlopen", fake_urlopen)

    analysis = _analysis(labels=["app-errors", "dup-1", "", "dup-1"])
    key = jira.create_issue(_settings(), analysis, source_logs=[{"a": 1}, {"b": 2}])
    assert key == "OPS-42"
    assert captured, "urlopen was not called"

    request, timeout = captured[0]
    assert timeout == jira._REQUEST_TIMEOUT
    assert request.full_url == "https://acme.atlassian.net/rest/api/3/issue"
    assert request.get_method() == "POST"

    auth = request.get_header("Authorization")
    assert auth is not None and auth.startswith("Basic ")
    # Email + token in the basic header; confirm token is NOT in the URL.
    assert "tok" not in request.full_url

    body = json.loads(request.data.decode("utf-8"))
    fields = body["fields"]
    assert fields["project"] == {"key": "OPS"}
    assert fields["issuetype"] == {"name": "Bug"}
    assert fields["summary"].startswith("[HIGH] Boom in notes")
    # Labels: blanks and duplicates dropped, capped at 10.
    assert fields["labels"] == ["app-errors", "dup-1"]
    # Description is ADF, not markdown.
    assert fields["description"]["type"] == "doc"
    assert fields["description"]["version"] == 1
    serialized = json.dumps(fields["description"])
    assert "# " not in serialized  # no markdown heading
    assert "Root cause" in serialized
    assert "Suspected locations" in serialized
    assert "Fix steps" in serialized


def test_create_issue_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jira,
        "load_secret",
        lambda _id: {
            "base_url": "https://acme.atlassian.net",
            "email": "ops@example.com",
            "api_token": "tok",
        },
    )
    error = jira.JiraError("HTTP 401 from Jira")
    monkeypatch.setattr(jira, "_post_create_issue", lambda *a, **k: (_ for _ in ()).throw(error))
    assert jira.create_issue(_settings(), _analysis()) is None


def test_create_issue_returns_none_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jira,
        "load_secret",
        lambda _id: {
            "base_url": "https://acme.atlassian.net",
            "email": "ops@example.com",
            "api_token": "tok",
        },
    )
    monkeypatch.setattr(jira, "_post_create_issue", lambda *a, **k: {"id": "1"})
    assert jira.create_issue(_settings(), _analysis()) is None


def test_create_issue_does_not_send_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a token in the secret we bail before touching the network."""
    monkeypatch.setattr(jira, "load_secret", lambda _id: {"base_url": "x", "email": "y"})

    called = False

    def must_not_run(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(jira, "_post_create_issue", must_not_run)
    assert jira.create_issue(_settings(), _analysis()) is None
    assert called is False


def test_browse_url_builds_correct_link() -> None:
    assert (
        jira.browse_url("https://acme.atlassian.net", "OPS-1")
        == "https://acme.atlassian.net/browse/OPS-1"
    )
    assert (
        jira.browse_url("https://acme.atlassian.net/", "OPS-2")
        == "https://acme.atlassian.net/browse/OPS-2"
    )
    assert jira.browse_url("https://acme.atlassian.net", "") == ""


def test_adf_description_includes_browse_link() -> None:
    body = jira._build_adf_description(
        _analysis(),
        environment="staging",
        alarm_name="alarm-1",
        fingerprint="fp1",
        occurrences=3,
        jira_browse_url="https://acme.atlassian.net/browse/OPS-1",
    )
    serialized = json.dumps(body)
    assert "https://acme.atlassian.net/browse/OPS-1" in serialized
    # ADF link is rendered as a node with the link mark, not as text.
    assert '"attrs"' in serialized and '"href"' in serialized


def test_coerce_labels_dedupes_and_caps() -> None:
    assert jira._coerce_labels(
        ["a", "b", "a", "", "  ", "c", "d", "e", "f", "g", "h", "i", "j", "k"],
    ) == [
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "j",
    ]
    assert jira._coerce_labels("not a list") == []
    assert jira._coerce_labels(None) == []


def test_settings_from_env_requires_jira_vars_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When JIRA_ENABLED=true, missing JIRA_PROJECT_KEY must fail with ConfigError."""
    from triage.settings import ConfigError

    for name in (
        "CW_LOG_GROUP",
        "DEDUP_TABLE",
        "LLM_SECRET_ID",
        "SNS_TOPIC_ARN",
        "JIRA_SECRET_ID",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("JIRA_ENABLED", "true")
    monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_settings_allows_empty_jira_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CW_LOG_GROUP",
        "DEDUP_TABLE",
        "LLM_SECRET_ID",
        "SNS_TOPIC_ARN",
        "JIRA_SECRET_ID",
        "JIRA_PROJECT_KEY",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("JIRA_ENABLED", "true")
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    settings = Settings.from_env()
    assert settings.jira_base_url == ""


def test_resolve_base_url_prefers_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(jira_base_url="https://from-env.atlassian.net")
    assert jira.resolve_base_url(settings) == "https://from-env.atlassian.net"


def test_resolve_base_url_falls_back_to_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(jira_base_url="")
    monkeypatch.setattr(
        jira,
        "load_secret",
        lambda _sid: {
            "base_url": "https://from-secret.atlassian.net",
            "email": "a@b.com",
            "api_token": "tok",
        },
    )
    assert jira.resolve_base_url(settings) == "https://from-secret.atlassian.net"
