"""Create a Jira Cloud issue from a triage report (optional).

The function is a thin REST client over ``urllib``: no third-party
dependencies, so it matches the rest of the Lambda. The API token, the
user's email and the Jira base URL are all read from a Secrets Manager
JSON blob (``base_url`` / ``email`` / ``api_token``); the Lambda's role
has ``secretsmanager:GetSecretValue`` only on the specific secret's ARN.

A failure here never blocks notification: ``create_issue`` logs a single
warning and returns ``None`` so the caller can keep the email path alive.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from triage.llm import Analysis
from triage.settings import ConfigError, Settings, load_secret

logger = logging.getLogger("triage.jira")

_REQUEST_TIMEOUT = 10  # seconds; Jira Cloud is fast in normal conditions


class JiraError(RuntimeError):
    """Raised for transport / HTTP / configuration problems talking to Jira."""


def _basic_auth_header(email: str, api_token: str) -> str:
    token = base64.b64encode(f"{email}:{api_token}".encode()).decode("ascii")
    return f"Basic {token}"


def _coerce_labels(value: Any, limit: int = 10) -> list[str]:
    """Return up to ``limit`` non-empty string labels, dropping duplicates."""
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        label = str(item).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
        if len(out) >= limit:
            break
    return out


def _plain_paragraph(text: str) -> dict[str, Any]:
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": text}],
    }


def _bullet_list(items: list[str]) -> dict[str, Any] | None:
    cleaned = [item.strip() for item in items if item and item.strip()]
    if not cleaned:
        return None
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [_plain_paragraph(item)],
            }
            for item in cleaned
        ],
    }


def _build_adf_description(
    analysis: Analysis,
    *,
    environment: str,
    alarm_name: str,
    fingerprint: str,
    occurrences: int,
    jira_browse_url: str,
) -> dict[str, Any]:
    """Return an Atlassian Document Format (ADF) body for the issue.

    The browse URL is rendered as an ADF link node so Jira renders it as
    a clickable shortcut to the issue once it exists. It only resolves
    to a real key after the create call, so callers pass the URL built
    from the secret's ``base_url``; the path component is intentionally
    omitted (filled by the caller after creation).
    """
    content: list[dict[str, Any]] = [
        _plain_paragraph(
            f"Environment: {environment}\n"
            f"Alarm: {alarm_name}\n"
            f"Fingerprint: {fingerprint}\n"
            f"Occurrences: {occurrences}\n"
            f"Severity: {analysis.severity.upper()} (model confidence: {analysis.confidence})"
        ),
        {
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Root cause"}],
        },
        _plain_paragraph(analysis.root_cause or "(not provided)"),
    ]
    locations = _bullet_list(analysis.suspected_locations)
    if locations:
        content.append(
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "Suspected locations"}],
            }
        )
        content.append(locations)

    fix_steps = _bullet_list(analysis.fix_steps)
    if fix_steps:
        content.append(
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "Fix steps"}],
            }
        )
        content.append(fix_steps)

    repro_steps = _bullet_list(analysis.repro_steps)
    if repro_steps:
        content.append(
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "Reproduction"}],
            }
        )
        content.append(repro_steps)

    if analysis.proposed_fix:
        content.append(
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "Proposed patch"}],
            }
        )
        content.append(
            {
                "type": "codeBlock",
                "attrs": {"language": "diff"},
                "content": [{"type": "text", "text": analysis.proposed_fix}],
            }
        )

    # The self-link is appended at the bottom; the caller fills in the key.
    if jira_browse_url:
        content.append(
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "Filed automatically by the turboai-notes error-triage Lambda. ",
                    },
                    {
                        "type": "text",
                        "text": "Open in Jira",
                        "marks": [{"type": "link", "attrs": {"href": jira_browse_url}}],
                    },
                ],
            }
        )

    return {"type": "doc", "version": 1, "content": content}


def _post_create_issue(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # Body is intentionally omitted from the log message: the API token
        # is sent in the Authorization header and Jira echoes the URL, so
        # logging the raw body would risk leaking the request id or the
        # project key alongside the auth context.
        body = exc.read().decode(errors="replace")
        raise JiraError(f"HTTP {exc.code} from Jira ({len(body)} bytes in response body)") from exc
    except urllib.error.URLError as exc:
        raise JiraError(f"network error talking to Jira: {exc.reason}") from exc

    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise JiraError("Jira returned a non-JSON response body") from exc
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _resolve_credentials(settings: Settings) -> tuple[str, str, str]:
    """Return ``(base_url, email, api_token)`` from the secret.

    The secret value comes from Secrets Manager, never from the function
    configuration, so the token never sits in ``lambda:GetFunctionConfiguration``.
    """
    if not settings.jira_secret_id:
        raise ConfigError("JIRA_SECRET_ID is required when JIRA_ENABLED is true")
    try:
        secret = load_secret(settings.jira_secret_id)
    except Exception as exc:  # noqa: BLE001 - cache + network errors all become JiraError
        raise JiraError(f"failed to read Jira secret: {exc}") from exc

    base_url = str(secret.get("base_url", "")).strip().rstrip("/")
    email = str(secret.get("email", "")).strip()
    api_token = str(secret.get("api_token", "")).strip()
    if not base_url or not email or not api_token:
        raise JiraError("Jira secret is missing one of base_url/email/api_token")
    return base_url, email, api_token


def _browser_url(base_url: str, issue_key: str | None) -> str:
    if not issue_key:
        return ""
    return f"{base_url.rstrip('/')}/browse/{issue_key}"


def create_issue(
    settings: Settings,
    analysis: Analysis,
    source_logs: list[dict[str, Any]] | None = None,
) -> str | None:
    """Create a Jira issue for the analysis and return its key (e.g. ``"BACK-42"``).

    Returns ``None`` when Jira is disabled, the secret is malformed, the
    create call fails, or ``settings.dry_run`` is true. Never raises;
    callers can keep the email path alive regardless.
    """
    if not settings.jira_enabled:
        return None
    if settings.dry_run:
        logger.info("Jira creation skipped: DRY_RUN")
        return None
    if not settings.jira_project_key:
        logger.warning("Jira creation skipped: JIRA_PROJECT_KEY is empty")
        return None

    try:
        base_url, email, api_token = _resolve_credentials(settings)
    except (ConfigError, JiraError) as exc:
        logger.warning("Jira credentials unavailable: %s", exc)
        return None

    issue_type = settings.jira_issue_type or "Bug"
    payload: dict[str, Any] = {
        "fields": {
            "project": {"key": settings.jira_project_key},
            "summary": f"[{analysis.severity.upper()}] {analysis.summary}"[:255],
            "issuetype": {"name": issue_type},
            "description": _build_adf_description(
                analysis,
                environment=settings.environment,
                alarm_name="(see logs)",  # alarm_name is plumbed via source_logs in handler
                fingerprint="",
                occurrences=len(source_logs) if source_logs else 0,
                jira_browse_url="",  # key not known yet
            ),
            "labels": _coerce_labels(getattr(analysis, "labels", []) or []),
        }
    }

    url = f"{base_url}/rest/api/3/issue"
    headers = {
        "Authorization": _basic_auth_header(email, api_token),
        "User-Agent": "turboai-notes-triage-lambda/1.0",
    }
    try:
        response = _post_create_issue(url, payload, headers)
    except JiraError as exc:
        logger.warning("Jira create_issue failed: %s", exc)
        return None

    issue_key = str(response.get("key", "")).strip() or None
    if not issue_key:
        logger.warning(
            "Jira create_issue returned no key (response keys: %s)", list(response.keys())
        )
        return None
    logger.info(
        "Jira issue %s created (%s browse url: %s)",
        issue_key,
        base_url,
        _browser_url(base_url, issue_key),
    )
    return issue_key


def browse_url(base_url: str, issue_key: str) -> str:
    """Public helper: build the operator-facing ``/browse/...`` link."""
    return _browser_url(base_url, issue_key)


def resolve_base_url(settings: Settings) -> str:
    """Return ``JIRA_BASE_URL`` or the secret's ``base_url`` (never raises)."""
    if settings.jira_base_url:
        return settings.jira_base_url.rstrip("/")
    if not settings.jira_secret_id:
        return ""
    try:
        base_url, _email, _token = _resolve_credentials(settings)
    except (ConfigError, JiraError):
        return ""
    return base_url
