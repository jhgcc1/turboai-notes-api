"""Prompt construction and defensive parsing of model output."""

from __future__ import annotations

import json
from typing import Any

import pytest
from triage import llm
from triage.httpjson import HttpError
from triage.logs import ErrorGroup, LogEvent

VALID = {
    "severity": "high",
    "summary": "Category lookup returns None for new users",
    "root_cause": "ensure_default_categories is not called before filtering.",
    "suspected_locations": ["apps/notes/views.py:73"],
    "proposed_fix": "--- a/apps/notes/views.py\n+++ b/apps/notes/views.py",
    "fix_steps": ["Call ensure_default_categories first"],
    "repro_steps": ["Register a new user", "POST /api/notes/seed/"],
    "confidence": "high",
}


def _group() -> ErrorGroup:
    event = LogEvent(
        timestamp="2026-08-10T12:00:00Z",
        message="[ERROR] boom",
        logger="apps.error",
        route="/api/notes/{id}/",
        method="GET",
        status="500",
        error_type="ValueError",
        fingerprint="abc123",
        request_id="req-1",
        traceback="Traceback...\nValueError: boom",
    )
    return ErrorGroup(fingerprint="abc123", events=[event])


def test_parse_analysis_reads_all_fields() -> None:
    analysis = llm.parse_analysis(json.dumps(VALID), "MiniMax-M2.1")
    assert analysis.severity == "high"
    assert analysis.suspected_locations == ["apps/notes/views.py:73"]
    assert analysis.repro_steps
    assert analysis.degraded is False


def test_parse_analysis_strips_markdown_fence() -> None:
    raw = f"Here you go:\n```json\n{json.dumps(VALID)}\n```\nHope that helps."
    assert llm.parse_analysis(raw, "m").severity == "high"


def test_parse_analysis_strips_think_block() -> None:
    raw = f"<think>reasoning about it</think>{json.dumps(VALID)}"
    assert llm.parse_analysis(raw, "m").summary.startswith("Category lookup")


def test_parse_analysis_handles_braces_inside_strings() -> None:
    payload = dict(VALID, root_cause="dict literal {'a': 1} confused the parser")
    assert llm.parse_analysis(json.dumps(payload), "m").root_cause.startswith("dict literal")


def test_parse_analysis_degrades_on_garbage() -> None:
    analysis = llm.parse_analysis("no json at all", "m")
    assert analysis.degraded is True
    assert "unparsable" in analysis.summary


def test_parse_analysis_degrades_on_broken_json() -> None:
    assert llm.parse_analysis('{"severity": "high"', "m").degraded is True


def test_parse_analysis_rejects_unknown_severity() -> None:
    assert llm.parse_analysis(json.dumps({"severity": "apocalyptic"}), "m").severity == "medium"


def test_parse_analysis_ignores_non_list_fields() -> None:
    analysis = llm.parse_analysis(json.dumps({"fix_steps": "not a list"}), "m")
    assert analysis.fix_steps == []


def test_build_user_prompt_includes_code_and_index() -> None:
    prompt = llm.build_user_prompt(_group(), "staging", "alarm-1", "apps/notes/views.py", "1 | x")
    assert "apps/notes/views.py" in prompt
    assert "1 | x" in prompt
    assert "alarm-1" in prompt


def test_analyze_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 30):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return {"choices": [{"message": {"content": json.dumps(VALID)}}]}

    monkeypatch.setattr(llm, "post_json", fake_post)
    analysis = llm.analyze(
        _group(),
        api_key="secret-key",
        base_url="https://api.minimax.io/v1/",
        model="MiniMax-M2.1",
        environment="staging",
        alarm_name="alarm-1",
        repo_index="index",
        code_context="code",
    )
    assert analysis.severity == "high"
    assert captured["url"] == "https://api.minimax.io/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["payload"]["model"] == "MiniMax-M2.1"


def test_analyze_degrades_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args: Any, **kwargs: Any):
        raise HttpError(500, "upstream exploded", "https://api.minimax.io/v1/chat/completions")

    monkeypatch.setattr(llm, "post_json", fake_post)
    analysis = llm.analyze(
        _group(),
        api_key="k",
        base_url="https://api.minimax.io/v1",
        model="m",
        environment="staging",
        alarm_name="a",
        repo_index="",
        code_context="",
    )
    assert analysis.degraded is True
    assert "provider error" in analysis.summary


def test_analyze_degrades_on_empty_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "post_json", lambda *a, **k: {"choices": [{"message": {}}]})
    analysis = llm.analyze(
        _group(),
        api_key="k",
        base_url="u",
        model="m",
        environment="staging",
        alarm_name="a",
        repo_index="",
        code_context="",
    )
    assert analysis.degraded is True
