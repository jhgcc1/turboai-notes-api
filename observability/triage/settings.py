"""Environment and secret resolution for the triage Lambda.

The MiniMax key lives in Secrets Manager, never in the function's environment:
Lambda env vars are readable by anyone holding
``lambda:GetFunctionConfiguration``.
"""

from __future__ import annotations

import functools
import json
import os
from dataclasses import dataclass
from typing import Any


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


@functools.lru_cache(maxsize=8)
def load_secret(secret_id: str) -> dict[str, Any]:
    """Fetch and parse a JSON secret, cached for the life of the container."""
    import boto3

    client = boto3.client("secretsmanager")
    raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"secret {secret_id} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"secret {secret_id} must be a JSON object")
    return parsed


@dataclass(frozen=True)
class Settings:
    log_group: str
    environment: str
    dedup_table: str
    llm_secret_id: str
    llm_base_url: str
    llm_model: str
    sns_topic_arn: str
    ses_from: str
    ses_to: str
    repo_root: str
    lookback_minutes: int
    max_events: int
    dedup_ttl_hours: int
    resend_every: int
    code_context_chars: int
    code_window_lines: int
    dry_run: bool

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            log_group=_require("CW_LOG_GROUP"),
            environment=os.getenv("ENVIRONMENT", "staging"),
            dedup_table=_require("DEDUP_TABLE"),
            llm_secret_id=_require("LLM_SECRET_ID"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.minimax.io/v1"),
            llm_model=os.getenv("LLM_MODEL", "MiniMax-M2.1"),
            sns_topic_arn=_require("SNS_TOPIC_ARN"),
            # SES is optional: unset means "notify through SNS only".
            ses_from=os.getenv("SES_FROM", "").strip(),
            ses_to=os.getenv("SES_TO", "").strip(),
            repo_root=os.getenv(
                "REPO_ROOT", os.path.join(os.path.dirname(os.path.dirname(__file__)), "repo")
            ),
            lookback_minutes=int(os.getenv("LOOKBACK_MINUTES", "15")),
            max_events=int(os.getenv("MAX_EVENTS", "25")),
            dedup_ttl_hours=int(os.getenv("DEDUP_TTL_HOURS", "72")),
            resend_every=int(os.getenv("RESEND_EVERY", "10")),
            code_context_chars=int(os.getenv("CODE_CONTEXT_CHARS", "24000")),
            code_window_lines=int(os.getenv("CODE_WINDOW_LINES", "40")),
            dry_run=_flag("DRY_RUN"),
        )
