"""CloudWatch console deep-link builders."""

from __future__ import annotations

import pytest
from triage import cloudwatch


def test_console_encode_uses_dollar_252f_for_slashes() -> None:
    encoded = cloudwatch.console_encode("/turboai/notes/staging/api")
    assert "$252F" in encoded
    assert "/" not in encoded


def test_build_log_group_url() -> None:
    url = cloudwatch.build_log_group_url("/turboai/notes/staging/api", region="us-east-2")
    assert url.startswith(
        "https://us-east-2.console.aws.amazon.com/cloudwatch/home?region=us-east-2#"
    )
    assert "logsV2:log-groups/log-group/" in url
    assert "$252Fturboai$252Fnotes$252Fstaging$252Fapi" in url


def test_build_log_stream_url() -> None:
    url = cloudwatch.build_log_stream_url(
        "/turboai/notes/staging/api",
        "ecs/api/abc",
        region="us-east-2",
    )
    assert "/log-events/" in url
    assert "$252F" in url  # stream path also encoded


def test_build_insights_url_filters_fingerprint_and_request_id() -> None:
    url = cloudwatch.build_insights_url(
        "/turboai/notes/staging/api",
        region="us-east-2",
        fingerprint="abc123",
        request_id="req-9",
        lookback_seconds=900,
    )
    assert "logs-insights$3FqueryDetail$3D" in url
    assert "fingerprint" in url
    assert "abc123" in url
    assert "req-9" in url
    assert "us-east-2" in url


def test_build_cloudwatch_url_prefers_insights_when_fingerprint() -> None:
    url = cloudwatch.build_cloudwatch_url(
        "/turboai/notes/prod/api",
        region="us-east-2",
        log_stream="stream-1",
        fingerprint="fp1",
    )
    assert "logs-insights" in url
    assert "fp1" in url


def test_build_cloudwatch_url_falls_back_to_stream() -> None:
    url = cloudwatch.build_cloudwatch_url(
        "/turboai/notes/staging/api",
        region="us-east-2",
        log_stream="ecs/api/x",
    )
    assert "/log-events/" in url


def test_build_cloudwatch_url_falls_back_to_group() -> None:
    url = cloudwatch.build_cloudwatch_url("/turboai/notes/staging/api", region="us-east-2")
    assert "log-groups/log-group/" in url
    assert "log-events" not in url


def test_url_from_log_events() -> None:
    url = cloudwatch.url_from_log_events(
        "/turboai/notes/staging/api",
        [{"fingerprint": "zz", "request_id": "r1", "log_stream": "s"}],
        region="us-east-2",
        lookback_minutes=15,
    )
    assert "zz" in url
    assert "logs-insights" in url


def test_url_from_log_events_empty() -> None:
    url = cloudwatch.url_from_log_events("/turboai/notes/staging/api", None, region="us-east-2")
    assert "log-groups/log-group/" in url


def test_resolve_region_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    assert cloudwatch.resolve_region() == "us-east-2"
    assert cloudwatch.resolve_region("eu-west-1") == "eu-west-1"
