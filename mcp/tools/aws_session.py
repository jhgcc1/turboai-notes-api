"""AWS SSO helpers with profile bootstrap for account 615737882760."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    ProfileNotFound,
    SSOTokenLoadError,
    TokenRetrievalError,
    UnauthorizedSSOTokenError,
)

import config


@functools.lru_cache(maxsize=1)
def get_session() -> boto3.Session:
    return boto3.Session(profile_name=config.AWS_PROFILE, region_name=config.AWS_REGION)


def reset_session() -> None:
    get_session.cache_clear()


def get_client(service: str) -> Any:
    return get_session().client(service)


def ensure_profile() -> dict[str, Any]:
    """Create ~/.aws/config SSO profile if missing (needs AWS_SSO_START_URL)."""
    aws_config = Path.home() / ".aws" / "config"
    aws_config.parent.mkdir(parents=True, exist_ok=True)
    marker = f"[profile {config.AWS_PROFILE}]"
    text = aws_config.read_text() if aws_config.exists() else ""
    if marker in text:
        return {"created": False, "profile": config.AWS_PROFILE, "message": "Profile already exists"}
    if not config.AWS_SSO_START_URL:
        return {
            "created": False,
            "profile": config.AWS_PROFILE,
            "error": "AWS_SSO_START_URL is not set",
            "action": (
                "Set AWS_SSO_START_URL in mcp/.env or Cursor MCP env, then call aws_sso_login again. "
                "Or run: backend/scripts/aws-sso-bootstrap.sh"
            ),
        }
    block = f"""

{marker}
sso_start_url = {config.AWS_SSO_START_URL}
sso_region = {config.AWS_SSO_REGION}
sso_account_id = {config.AWS_ACCOUNT_ID}
sso_role_name = AdministratorAccess
region = {config.AWS_REGION}
output = json
"""
    with aws_config.open("a", encoding="utf-8") as fh:
        fh.write(block)
    reset_session()
    return {"created": True, "profile": config.AWS_PROFILE, "message": "SSO profile written"}


def check_sso() -> dict[str, Any]:
    ensure = ensure_profile()
    if ensure.get("error"):
        return {"valid": False, "env_label": "UNKNOWN", **ensure}
    try:
        identity = get_client("sts").get_caller_identity()
        return {
            "valid": True,
            "account": identity.get("Account"),
            "arn": identity.get("Arn"),
            "profile": config.AWS_PROFILE,
            "region": config.AWS_REGION,
            "expected_account": config.AWS_ACCOUNT_ID,
            "profile_bootstrap": ensure,
        }
    except (
        SSOTokenLoadError,
        TokenRetrievalError,
        UnauthorizedSSOTokenError,
        NoCredentialsError,
        ProfileNotFound,
    ) as exc:
        return {
            "valid": False,
            "error": str(exc),
            "action": f"Call aws_sso_login or run: aws sso login --profile {config.AWS_PROFILE}",
            "profile_bootstrap": ensure,
        }
    except ClientError as exc:
        return {"valid": False, "error": str(exc), "profile_bootstrap": ensure}


def sso_login_blocking(timeout_seconds: int = 180) -> dict[str, Any]:
    ensure = ensure_profile()
    if ensure.get("error"):
        return {"ok": False, **ensure}
    cmd = ["aws", "sso", "login", "--profile", config.AWS_PROFILE]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
        reset_session()
        status = check_sso()
        return {
            "ok": result.returncode == 0 and status.get("valid"),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "sso": status,
            "profile_bootstrap": ensure,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "aws sso login timed out — complete browser login and retry aws_sso_status",
            "profile_bootstrap": ensure,
        }
