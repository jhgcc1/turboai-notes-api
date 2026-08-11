"""GitHub tracking branch / draft PR tests (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from triage import github_pr
from triage.httpjson import HttpError
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
        "jira_project_key": "KAN",
        "jira_issue_type": "Bug",
        "jira_secret_id": "jira-secret",
        "github_pr_enabled": True,
        "github_secret_id": "github-secret",
        "github_base_branch": "develop",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_open_tracking_returns_none_when_disabled() -> None:
    assert github_pr.open_tracking_branch(_settings(github_pr_enabled=False), "KAN-1") is None


def test_open_tracking_returns_none_in_dry_run() -> None:
    assert github_pr.open_tracking_branch(_settings(dry_run=True), "KAN-1") is None


def test_open_tracking_returns_none_when_key_empty() -> None:
    assert github_pr.open_tracking_branch(_settings(), "") is None


def test_open_tracking_handles_secret_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert github_pr.open_tracking_branch(_settings(), "KAN-1") is None


def test_open_tracking_handles_placeholder_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {"token": "REPLACE_ME", "owner": "jhgcc1", "repo": "turboai-notes-api"},
    )
    assert github_pr.open_tracking_branch(_settings(), "KAN-1") is None


def test_open_tracking_reuses_existing_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {"token": "ghp_x", "owner": "jhgcc1", "repo": "turboai-notes-api"},
    )
    monkeypatch.setattr(github_pr, "_ref_exists", lambda *a, **k: True)
    monkeypatch.setattr(
        github_pr,
        "_find_open_pr",
        lambda *a, **k: ("https://github.com/jhgcc1/turboai-notes-api/pull/9", 9),
    )
    result = github_pr.open_tracking_branch(_settings(), "KAN-12")
    assert result is not None
    assert result.reused_existing is True
    assert result.branch == "fix/KAN-12"
    assert result.pr_url.endswith("/pull/9")
    assert result.created_branch is False


def test_open_tracking_creates_branch_and_draft_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {"token": "ghp_x", "owner": "jhgcc1", "repo": "turboai-notes-api"},
    )
    monkeypatch.setattr(github_pr, "_ref_exists", lambda *a, **k: False)
    monkeypatch.setattr(github_pr, "_get_ref_sha", lambda *a, **k: "base-sha")
    monkeypatch.setattr(github_pr, "_create_empty_commit", lambda *a, **k: "new-sha")
    created_refs: list[tuple[str, str]] = []

    def fake_create_ref(_api: str, _headers: dict[str, str], branch: str, sha: str) -> None:
        created_refs.append((branch, sha))

    monkeypatch.setattr(github_pr, "_create_ref", fake_create_ref)
    monkeypatch.setattr(
        github_pr,
        "_create_draft_pr",
        lambda *a, **k: ("https://github.com/jhgcc1/turboai-notes-api/pull/3", 3),
    )
    result = github_pr.open_tracking_branch(
        _settings(),
        "KAN-12",
        summary="Null deref in notes list",
        jira_browse_url="https://acme.atlassian.net/browse/KAN-12",
        cloudwatch_url="https://console.aws.amazon.com/cloudwatch",
    )
    assert result is not None
    assert result.created_branch is True
    assert result.created_pr is True
    assert created_refs == [("fix/KAN-12", "new-sha")]
    assert result.pr_number == 3
    assert result.branch_url.endswith("tree/fix/KAN-12")


def test_pr_body_links_jira_and_cloudwatch() -> None:
    body = github_pr._pr_body(  # noqa: SLF001
        "KAN-12",
        jira_browse_url="https://acme.atlassian.net/browse/KAN-12",
        cloudwatch_url="https://cw.example/logs",
    )
    assert "KAN-12" in body
    assert "https://acme.atlassian.net/browse/KAN-12" in body
    assert "https://cw.example/logs" in body
    assert "turboai-notes-web" in body


def test_open_tracking_keeps_branch_when_pr_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {"token": "ghp_x", "owner": "jhgcc1", "repo": "turboai-notes-api"},
    )
    monkeypatch.setattr(github_pr, "_ref_exists", lambda *a, **k: False)
    monkeypatch.setattr(github_pr, "_get_ref_sha", lambda *a, **k: "base-sha")
    monkeypatch.setattr(github_pr, "_create_empty_commit", lambda *a, **k: "new-sha")
    monkeypatch.setattr(github_pr, "_create_ref", lambda *a, **k: None)

    def boom(*_a: Any, **_k: Any) -> tuple[str, int | None]:
        raise github_pr.GitHubError("failed to create draft PR: HTTP 422")

    monkeypatch.setattr(github_pr, "_create_draft_pr", boom)
    result = github_pr.open_tracking_branch(_settings(), "KAN-12", summary="x")
    assert result is not None
    assert result.created_branch is True
    assert result.created_pr is False
    assert result.branch_url.endswith("fix/KAN-12")
    assert result.pr_url == ""


def test_open_tracking_swallows_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {"token": "ghp_x", "owner": "jhgcc1", "repo": "turboai-notes-api"},
    )
    monkeypatch.setattr(
        github_pr,
        "_ref_exists",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("surprise")),
    )
    assert github_pr.open_tracking_branch(_settings(), "KAN-12") is None


def test_as_comment_text_includes_urls() -> None:
    text = github_pr.as_comment_text(
        github_pr.TrackingResult(
            issue_key="KAN-1",
            branch="fix/KAN-1",
            branch_url="https://github.com/jhgcc1/turboai-notes-api/tree/fix/KAN-1",
            pr_url="https://github.com/jhgcc1/turboai-notes-api/pull/1",
            pr_number=1,
            created_branch=True,
            created_pr=True,
        )
    )
    assert "fix/KAN-1" in text
    assert "pull/1" in text


def test_create_empty_commit_posts_same_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(
        url: str, headers: dict[str, str] | None = None, timeout: int = 30
    ) -> dict[str, Any]:
        return {"tree": {"sha": "tree-1"}, "sha": "parent-1"}

    def fake_post(
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: int = 30,
    ) -> dict[str, Any]:
        calls.append((url, payload))
        return {"sha": "commit-2"}

    monkeypatch.setattr(github_pr, "get_json", fake_get)
    monkeypatch.setattr(github_pr, "post_json", fake_post)
    sha = github_pr._create_empty_commit(  # noqa: SLF001
        "https://api.github.com/repos/o/r",
        {"Authorization": "Bearer x"},
        parent_sha="parent-1",
        message="chore: open tracking branch for KAN-12",
    )
    assert sha == "commit-2"
    assert calls[0][1]["tree"] == "tree-1"
    assert calls[0][1]["parents"] == ["parent-1"]


def test_ref_exists_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise HttpError(404, "missing", "url")

    monkeypatch.setattr(github_pr, "get_json", boom)
    assert github_pr._ref_exists("https://api.github.com/repos/o/r", {}, "fix/KAN-1") is False  # noqa: SLF001


def test_ref_exists_true_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_pr, "get_json", lambda *a, **k: {"ref": "refs/heads/fix/KAN-1"})
    assert github_pr._ref_exists("https://api.github.com/repos/o/r", {}, "fix/KAN-1") is True  # noqa: SLF001


def test_settings_requires_github_secret_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CW_LOG_GROUP", "lg")
    monkeypatch.setenv("DEDUP_TABLE", "t")
    monkeypatch.setenv("LLM_SECRET_ID", "llm")
    monkeypatch.setenv("SNS_TOPIC_ARN", "arn")
    monkeypatch.setenv("GITHUB_PR_ENABLED", "true")
    monkeypatch.delenv("GITHUB_SECRET_ID", raising=False)
    from triage.settings import ConfigError

    with pytest.raises(ConfigError):
        Settings.from_env()


def test_resolve_credentials_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_pr, "load_secret", lambda _id: {"token": "x"})
    with pytest.raises(github_pr.GitHubError):
        github_pr._resolve_credentials(_settings())  # noqa: SLF001


def test_resolve_credentials_empty_secret_id() -> None:
    from triage.settings import ConfigError

    with pytest.raises(ConfigError):
        github_pr._resolve_credentials(_settings(github_secret_id=""))  # noqa: SLF001


def test_resolve_credentials_uses_secret_base_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {
            "token": "ghp_x",
            "owner": "jhgcc1",
            "repo": "turboai-notes-api",
            "base_branch": "main",
        },
    )
    token, owner, repo, base = github_pr._resolve_credentials(_settings())  # noqa: SLF001
    assert (token, owner, repo, base) == ("ghp_x", "jhgcc1", "turboai-notes-api", "main")


def test_get_ref_sha_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "get_json",
        lambda *a, **k: {"object": {"sha": "abc"}},
    )
    assert github_pr._get_ref_sha("https://api.github.com/repos/o/r", {}, "develop") == "abc"  # noqa: SLF001

    monkeypatch.setattr(
        github_pr,
        "get_json",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(404, "no", "u")),
    )
    with pytest.raises(github_pr.GitHubError):
        github_pr._get_ref_sha("https://api.github.com/repos/o/r", {}, "develop")  # noqa: SLF001

    monkeypatch.setattr(github_pr, "get_json", lambda *a, **k: {"object": {}})
    with pytest.raises(github_pr.GitHubError):
        github_pr._get_ref_sha("https://api.github.com/repos/o/r", {}, "develop")  # noqa: SLF001


def test_ref_exists_raises_on_non_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "get_json",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(500, "x", "u")),
    )
    with pytest.raises(github_pr.GitHubError):
        github_pr._ref_exists("https://api.github.com/repos/o/r", {}, "fix/x")  # noqa: SLF001


def test_find_open_pr_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "request_json",
        lambda *a, **k: {"data": [{"html_url": "https://github.com/o/r/pull/2", "number": 2}]},
    )
    url, number = github_pr._find_open_pr("https://api.github.com/repos/o/r", {}, "o", "fix/x")  # noqa: SLF001
    assert url.endswith("/pull/2")
    assert number == 2

    monkeypatch.setattr(
        github_pr,
        "request_json",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(403, "no", "u")),
    )
    assert github_pr._find_open_pr("https://api.github.com/repos/o/r", {}, "o", "fix/x") == (
        "",
        None,
    )  # noqa: SLF001

    monkeypatch.setattr(github_pr, "request_json", lambda *a, **k: {"data": []})
    assert github_pr._find_open_pr("https://api.github.com/repos/o/r", {}, "o", "fix/x") == (
        "",
        None,
    )  # noqa: SLF001

    monkeypatch.setattr(github_pr, "request_json", lambda *a, **k: {"unexpected": True})
    assert github_pr._find_open_pr("https://api.github.com/repos/o/r", {}, "o", "fix/x") == (
        "",
        None,
    )  # noqa: SLF001

    monkeypatch.setattr(github_pr, "request_json", lambda *a, **k: {})
    assert github_pr._find_open_pr("https://api.github.com/repos/o/r", {}, "o", "fix/x") == (
        "",
        None,
    )  # noqa: SLF001


def test_create_empty_commit_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "get_json",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(404, "x", "u")),
    )
    with pytest.raises(github_pr.GitHubError):
        github_pr._create_empty_commit(  # noqa: SLF001
            "https://api.github.com/repos/o/r",
            {},
            parent_sha="p",
            message="m",
        )

    monkeypatch.setattr(github_pr, "get_json", lambda *a, **k: {"tree": {}})
    with pytest.raises(github_pr.GitHubError):
        github_pr._create_empty_commit(  # noqa: SLF001
            "https://api.github.com/repos/o/r",
            {},
            parent_sha="p",
            message="m",
        )

    monkeypatch.setattr(github_pr, "get_json", lambda *a, **k: {"tree": {"sha": "t"}})
    monkeypatch.setattr(
        github_pr,
        "post_json",
        lambda *a, **k: (_ for _ in ()).throw(HttpError(500, "x", "u")),
    )
    with pytest.raises(github_pr.GitHubError):
        github_pr._create_empty_commit(  # noqa: SLF001
            "https://api.github.com/repos/o/r",
            {},
            parent_sha="p",
            message="m",
        )

    monkeypatch.setattr(github_pr, "post_json", lambda *a, **k: {})
    with pytest.raises(github_pr.GitHubError):
        github_pr._create_empty_commit(  # noqa: SLF001
            "https://api.github.com/repos/o/r",
            {},
            parent_sha="p",
            message="m",
        )


def test_create_ref_and_draft_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[dict[str, Any]] = []

    def fake_post(
        url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 30
    ) -> dict[str, Any]:
        posts.append(payload)
        if url.endswith("/git/refs"):
            return {"ref": payload["ref"]}
        return {"html_url": "https://github.com/o/r/pull/8", "number": 8}

    monkeypatch.setattr(github_pr, "post_json", fake_post)
    github_pr._create_ref("https://api.github.com/repos/o/r", {}, "fix/KAN-1", "sha")  # noqa: SLF001
    url, number = github_pr._create_draft_pr(  # noqa: SLF001
        "https://api.github.com/repos/o/r",
        {},
        title="[KAN-1] x",
        body="b",
        head="fix/KAN-1",
        base="develop",
    )
    assert url.endswith("/pull/8")
    assert number == 8
    assert posts[0]["ref"] == "refs/heads/fix/KAN-1"
    assert posts[1]["draft"] is True

    def conflict(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise HttpError(422, "exists", "u")

    monkeypatch.setattr(github_pr, "post_json", conflict)
    github_pr._create_ref("https://api.github.com/repos/o/r", {}, "fix/KAN-1", "sha")  # noqa: SLF001

    def hard_fail(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise HttpError(500, "x", "u")

    monkeypatch.setattr(github_pr, "post_json", hard_fail)
    with pytest.raises(github_pr.GitHubError):
        github_pr._create_ref("https://api.github.com/repos/o/r", {}, "fix/KAN-1", "sha")  # noqa: SLF001
    with pytest.raises(github_pr.GitHubError):
        github_pr._create_draft_pr(  # noqa: SLF001
            "https://api.github.com/repos/o/r",
            {},
            title="t",
            body="b",
            head="h",
            base="develop",
        )


def test_open_tracking_github_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_pr,
        "load_secret",
        lambda _id: {"token": "ghp_x", "owner": "jhgcc1", "repo": "turboai-notes-api"},
    )
    monkeypatch.setattr(github_pr, "_ref_exists", lambda *a, **k: False)
    monkeypatch.setattr(
        github_pr,
        "_get_ref_sha",
        lambda *a, **k: (_ for _ in ()).throw(github_pr.GitHubError("no develop")),
    )
    assert github_pr.open_tracking_branch(_settings(), "KAN-12") is None


def test_as_comment_text_without_pr() -> None:
    text = github_pr.as_comment_text(
        github_pr.TrackingResult(
            issue_key="KAN-1",
            branch="fix/KAN-1",
            branch_url="https://github.com/jhgcc1/turboai-notes-api/tree/fix/KAN-1",
            reused_existing=True,
        )
    )
    assert "none open yet" in text
