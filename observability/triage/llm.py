"""Ask MiniMax to debug an error against the real source code.

MiniMax exposes an OpenAI-compatible ``/chat/completions`` endpoint, so the
payload below is the standard chat shape and would work against any
OpenAI-compatible provider by swapping ``base_url`` and ``model``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from triage.httpjson import HttpError, post_json
from triage.logs import ErrorGroup

SEVERITIES = ("critical", "high", "medium", "low")

SYSTEM_PROMPT = """\
You are a senior Django engineer debugging a production error for the Turbo AI
Notes API.

Stack: Django 5 + Django REST Framework, PostgreSQL 16, deployed on AWS ECS
Fargate behind an ALB and CloudFront. Auth uses JWT in httpOnly cookies plus
django-axes lockout. Layout: apps/accounts (auth), apps/notes (notes and
categories CRUD, per-user ownership), config (settings, JSON logging, DRF
exception handler).

You are given the error logs, a file index of the repository, and the actual
source code around every traceback frame. Use the code — do not speculate about
lines you were not shown.

Rules:
1. Diagnose the true root cause from the provided source. If the code shown is
   insufficient, say so plainly and set confidence to "low".
2. Point at specific files and line numbers you were shown.
3. Propose a concrete fix. Prefer a unified diff against the quoted code. If a
   diff is not appropriate, give the replacement code block.
4. Severity by user impact: critical (data loss/outage), high (feature broken),
   medium (degraded), low (cosmetic or log noise).

Respond with ONLY a JSON object, no markdown fence and no prose:
{
  "severity": "critical|high|medium|low",
  "summary": "one line, max 120 chars",
  "root_cause": "2-5 sentences grounded in the code shown",
  "suspected_locations": ["apps/notes/views.py:42"],
  "proposed_fix": "unified diff or replacement code",
  "fix_steps": ["step 1", "step 2"],
  "repro_steps": ["step 1"],
  "confidence": "high|medium|low"
}"""

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass
class Analysis:
    severity: str = "medium"
    summary: str = "Unclassified application error"
    root_cause: str = "The model did not return a usable analysis."
    suspected_locations: list[str] = field(default_factory=list)
    proposed_fix: str = ""
    fix_steps: list[str] = field(default_factory=list)
    repro_steps: list[str] = field(default_factory=list)
    confidence: str = "low"
    model: str = ""
    degraded: bool = False
    # Optional labels (e.g. "app-errors", "fingerprint-bucket"). The Jira
    # integration is the only consumer today; keeping the field here means
    # we don't have to plumb labels through the call sites if other
    # downstreams want them later.
    labels: list[str] = field(default_factory=list)


def build_user_prompt(
    group: ErrorGroup,
    environment: str,
    alarm_name: str,
    repo_index: str,
    code_context: str,
) -> str:
    sample = group.representative
    others = [
        {
            "time": event.timestamp,
            "route": event.route,
            "status": event.status,
            "message": event.message[:300],
        }
        for event in group.events[:5]
    ]
    return (
        f"Environment: {environment}\n"
        f"CloudWatch alarm: {alarm_name}\n"
        f"Fingerprint: {group.fingerprint}\n"
        f"Occurrences in this window: {group.count}\n\n"
        f"## Failing request\n"
        f"logger: {sample.logger}\n"
        f"error_type: {sample.error_type}\n"
        f"route: {sample.method} {sample.route}\n"
        f"status: {sample.status}\n"
        f"message: {sample.message}\n\n"
        f"## Traceback\n{sample.traceback or '(none captured)'}\n\n"
        f"## Repository file index\n{repo_index}\n\n"
        f"## Source code around the traceback frames\n{code_context}\n\n"
        f"## Other recent occurrences\n{json.dumps(others, indent=2)}\n"
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first balanced JSON object out of a model response.

    Reasoning models wrap answers in prose or ``<think>`` blocks even when told
    not to, so locating the object beats parsing the whole body.
    """
    cleaned = _THINK_BLOCK.sub("", text).strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _as_str_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def parse_analysis(raw: str, model: str) -> Analysis:
    """Coerce a model response into an Analysis, never raising."""
    data = _extract_json_object(raw)
    if data is None:
        return Analysis(
            summary="LLM analysis unavailable (unparsable response)",
            root_cause=f"Raw model output:\n{raw[:1000]}",
            model=model,
            degraded=True,
        )

    severity = str(data.get("severity", "")).lower().strip()
    summary = str(data.get("summary", "")).strip()
    return Analysis(
        severity=severity if severity in SEVERITIES else "medium",
        summary=(summary or "Unclassified application error")[:120],
        root_cause=str(data.get("root_cause", "")).strip() or "Not provided.",
        suspected_locations=_as_str_list(data.get("suspected_locations"), 10),
        proposed_fix=str(data.get("proposed_fix", "")).strip(),
        fix_steps=_as_str_list(data.get("fix_steps"), 10),
        repro_steps=_as_str_list(data.get("repro_steps"), 10),
        confidence=str(data.get("confidence", "")).lower().strip() or "medium",
        model=model,
    )


def analyze(
    group: ErrorGroup,
    *,
    api_key: str,
    base_url: str,
    model: str,
    environment: str,
    alarm_name: str,
    repo_index: str,
    code_context: str,
    timeout: int = 60,
) -> Analysis:
    """Return the model's analysis, degrading gracefully if the call fails.

    An email carrying raw logs and no analysis still beats no email, so
    transport and parsing failures never propagate.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_user_prompt(
                    group, environment, alarm_name, repo_index, code_context
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 2000,
        # Keeps M-series chain-of-thought out of `content`, which would
        # otherwise sit in front of the JSON object we need to parse.
        "reasoning_split": True,
    }
    try:
        response = post_json(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            {"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except HttpError as exc:
        return Analysis(
            summary="LLM analysis unavailable (provider error)",
            root_cause=f"Call to {model} failed: {exc}",
            model=model,
            degraded=True,
        )

    choices = response.get("choices") or []
    content = ""
    if choices:
        content = str((choices[0].get("message") or {}).get("content") or "")
    if not content.strip():
        return Analysis(
            summary="LLM analysis unavailable (empty response)",
            root_cause=f"{model} returned no content.",
            model=model,
            degraded=True,
        )
    return parse_analysis(content, model)
