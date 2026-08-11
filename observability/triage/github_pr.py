"""Open a GitHub tracking branch (and draft PR) named after a Jira key.

Called only after a successful Jira ``create_issue``. The branch points at
``develop`` (configurable) with an empty commit so GitHub will accept a
draft PR that has no product code changes — investigation / future fix
tracking only.

Scope: ``turboai-notes-api`` (API repo). The web repo is out of scope; most
bugs the triage Lambda debugs live in the Django bundle.

Failures never raise: the email / Jira path must keep working if GitHub is
down, the secret is a placeholder, or the branch already exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

from triage.httpjson import HttpError, get_json, post_json, request_json
from triage.settings import ConfigError, Settings, load_secret

logger = logging.getLogger("triage.github_pr")

_REQUEST_TIMEOUT = 15
_API = "https://api.github.com"
_USER_AGENT = "turboai-notes-triage-lambda/1.0"


class GitHubError(RuntimeError):
    """Raised for transport / HTTP / configuration problems talking to GitHub."""


@dataclass(frozen=True)
class TrackingResult:
    """URLs opened (or already present) for a Jira key."""

    issue_key: str
    branch: str
    branch_url: str
    pr_url: str = ""
    pr_number: int | None = None
    created_branch: bool = False
    created_pr: bool = False
    reused_existing: bool = False


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }


def _resolve_credentials(settings: Settings) -> tuple[str, str, str, str]:
    """Return ``(token, owner, repo, base_branch)`` from the secret + settings."""
    if not settings.github_secret_id:
        raise ConfigError("GITHUB_SECRET_ID is required when GITHUB_PR_ENABLED is true")
    try:
        secret = load_secret(settings.github_secret_id)
    except Exception as exc:  # noqa: BLE001 - SM / parse errors become GitHubError
        raise GitHubError(f"failed to read GitHub secret: {exc}") from exc

    token = str(secret.get("token", "")).strip()
    owner = str(secret.get("owner", "")).strip()
    repo = str(secret.get("repo", "")).strip()
    if not token or not owner or not repo:
        raise GitHubError("GitHub secret is missing one of token/owner/repo")
    if token == "REPLACE_ME":
        raise GitHubError("GitHub secret still has placeholder token")

    base_branch = (
        str(secret.get("base_branch", "")).strip() or settings.github_base_branch or "develop"
    )
    return token, owner, repo, base_branch


def _repo_api(owner: str, repo: str) -> str:
    return f"{_API}/repos/{owner}/{repo}"


def _branch_url(owner: str, repo: str, branch: str) -> str:
    return f"https://github.com/{owner}/{repo}/tree/{branch}"


def _get_ref_sha(api: str, headers: dict[str, str], branch: str) -> str:
    encoded = quote(branch, safe="")
    try:
        data = get_json(f"{api}/git/ref/heads/{encoded}", headers=headers, timeout=_REQUEST_TIMEOUT)
    except HttpError as exc:
        raise GitHubError(f"failed to resolve ref heads/{branch}: HTTP {exc.status}") from exc
    sha = str((data.get("object") or {}).get("sha", "")).strip()
    if not sha:
        raise GitHubError(f"ref heads/{branch} returned no sha")
    return sha


def _ref_exists(api: str, headers: dict[str, str], branch: str) -> bool:
    encoded = quote(branch, safe="")
    try:
        get_json(f"{api}/git/ref/heads/{encoded}", headers=headers, timeout=_REQUEST_TIMEOUT)
        return True
    except HttpError as exc:
        if exc.status == 404:
            return False
        raise GitHubError(f"failed to check ref heads/{branch}: HTTP {exc.status}") from exc


def _find_open_pr(
    api: str, headers: dict[str, str], owner: str, branch: str
) -> tuple[str, int | None]:
    """Return ``(html_url, number)`` for an open PR with head ``owner:branch``."""
    head = quote(f"{owner}:{branch}", safe=":")
    url = f"{api}/pulls?state=open&head={head}&per_page=1"
    try:
        # list endpoints return arrays; request_json wraps non-dicts as {"data": ...}
        raw = request_json("GET", url, headers=headers, timeout=_REQUEST_TIMEOUT)
    except HttpError as exc:
        logger.warning("GitHub list pulls failed: HTTP %s", exc.status)
        return "", None
    items = raw.get("data") if "data" in raw and isinstance(raw.get("data"), list) else None
    if items is None and isinstance(raw, dict) and not raw:
        items = []
    # When GitHub returns a bare array, request_json stores it under "data".
    if items is None:
        # Unexpected object shape — treat as no PR.
        return "", None
    if not items:
        return "", None
    first = items[0] if isinstance(items[0], dict) else {}
    html = str(first.get("html_url", "")).strip()
    number = first.get("number")
    return html, int(number) if isinstance(number, int) else None


def _create_empty_commit(
    api: str,
    headers: dict[str, str],
    *,
    parent_sha: str,
    message: str,
) -> str:
    try:
        parent = get_json(
            f"{api}/git/commits/{parent_sha}", headers=headers, timeout=_REQUEST_TIMEOUT
        )
    except HttpError as exc:
        raise GitHubError(f"failed to read parent commit: HTTP {exc.status}") from exc
    tree_sha = str((parent.get("tree") or {}).get("sha", "")).strip()
    if not tree_sha:
        raise GitHubError("parent commit has no tree sha")
    try:
        created = post_json(
            f"{api}/git/commits",
            {
                "message": message,
                "tree": tree_sha,
                "parents": [parent_sha],
            },
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
    except HttpError as exc:
        raise GitHubError(f"failed to create empty commit: HTTP {exc.status}") from exc
    sha = str(created.get("sha", "")).strip()
    if not sha:
        raise GitHubError("create commit returned no sha")
    return sha


def _create_ref(api: str, headers: dict[str, str], branch: str, sha: str) -> None:
    try:
        post_json(
            f"{api}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
    except HttpError as exc:
        # 422 often means the ref already exists (race with another invocation).
        if exc.status in (409, 422):
            logger.info("GitHub ref heads/%s already exists (HTTP %s)", branch, exc.status)
            return
        raise GitHubError(f"failed to create ref heads/{branch}: HTTP {exc.status}") from exc


def _create_draft_pr(
    api: str,
    headers: dict[str, str],
    *,
    title: str,
    body: str,
    head: str,
    base: str,
) -> tuple[str, int | None]:
    try:
        created = post_json(
            f"{api}/pulls",
            {
                "title": title[:256],
                "body": body,
                "head": head,
                "base": base,
                "draft": True,
            },
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
    except HttpError as exc:
        raise GitHubError(f"failed to create draft PR: HTTP {exc.status}") from exc
    html = str(created.get("html_url", "")).strip()
    number = created.get("number")
    return html, int(number) if isinstance(number, int) else None


def _pr_body(
    issue_key: str,
    *,
    jira_browse_url: str,
    cloudwatch_url: str,
) -> str:
    lines = [
        f"Tracking branch for Jira **{issue_key}**.",
        "",
        "Opened automatically by the turboai-notes error-triage Lambda.",
        "No product code changes — empty commit so investigation / future fix",
        "can land on this branch.",
        "",
    ]
    if jira_browse_url:
        lines.append(f"- Jira: {jira_browse_url}")
    if cloudwatch_url:
        lines.append(f"- CloudWatch: {cloudwatch_url}")
    lines.extend(
        [
            "",
            "> Scope: `turboai-notes-api` only. Frontend (`turboai-notes-web`) is",
            "> out of scope for this automation.",
        ]
    )
    return "\n".join(lines)


def open_tracking_branch(
    settings: Settings,
    issue_key: str,
    *,
    summary: str = "",
    jira_browse_url: str = "",
    cloudwatch_url: str = "",
) -> TrackingResult | None:
    """Create ``fix/<issue_key>`` (+ draft PR) after a new Jira issue.

    Returns ``None`` when GitHub PR creation is disabled, dry-run is on, the
    secret is unusable, or transport fails. Never raises.
    """
    if not settings.github_pr_enabled:
        return None
    if settings.dry_run:
        logger.info("GitHub tracking branch skipped: DRY_RUN")
        return None
    key = (issue_key or "").strip()
    if not key:
        return None

    try:
        token, owner, repo, base_branch = _resolve_credentials(settings)
    except (ConfigError, GitHubError) as exc:
        logger.warning("GitHub credentials unavailable: %s", exc)
        return None

    api = _repo_api(owner, repo)
    headers = _gh_headers(token)
    branch = f"fix/{key}"
    branch_url = _branch_url(owner, repo, branch)

    try:
        if _ref_exists(api, headers, branch):
            pr_url, pr_number = _find_open_pr(api, headers, owner, branch)
            logger.info(
                "GitHub branch %s already exists; pr=%s",
                branch,
                pr_url or "(none)",
            )
            return TrackingResult(
                issue_key=key,
                branch=branch,
                branch_url=branch_url,
                pr_url=pr_url,
                pr_number=pr_number,
                reused_existing=True,
            )

        base_sha = _get_ref_sha(api, headers, base_branch)
        commit_sha = _create_empty_commit(
            api,
            headers,
            parent_sha=base_sha,
            message=f"chore: open tracking branch for {key}",
        )
        _create_ref(api, headers, branch, commit_sha)

        title = f"[{key}] {(summary or 'investigation').strip()}"[:256]
        body = _pr_body(
            key,
            jira_browse_url=jira_browse_url,
            cloudwatch_url=cloudwatch_url,
        )
        try:
            pr_url, pr_number = _create_draft_pr(
                api,
                headers,
                title=title,
                body=body,
                head=branch,
                base=base_branch,
            )
            created_pr = bool(pr_url)
        except GitHubError as exc:
            # Branch is enough when GitHub rejects an identical-tree PR.
            logger.warning("GitHub draft PR skipped after branch create: %s", exc)
            pr_url, pr_number, created_pr = "", None, False

        logger.info(
            "GitHub tracking branch %s created (pr=%s)",
            branch,
            pr_url or "(none)",
        )
        return TrackingResult(
            issue_key=key,
            branch=branch,
            branch_url=branch_url,
            pr_url=pr_url,
            pr_number=pr_number,
            created_branch=True,
            created_pr=created_pr,
        )
    except GitHubError as exc:
        logger.warning("GitHub tracking branch failed for %s: %s", key, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - never block email/Jira
        logger.warning("GitHub tracking branch unexpected error for %s: %s", key, exc)
        return None


def as_comment_text(result: TrackingResult) -> str:
    """Plain-text summary suitable for a Jira comment body."""
    lines = [
        "GitHub tracking (api repo turboai-notes-api; web out of scope):",
        f"Branch: {result.branch_url}",
    ]
    if result.pr_url:
        lines.append(f"Draft PR: {result.pr_url}")
    elif result.reused_existing:
        lines.append("Draft PR: (none open yet)")
    return "\n".join(lines)
