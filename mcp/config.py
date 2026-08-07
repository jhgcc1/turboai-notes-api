"""Turbo Notes AWS MCP configuration (account 615737882760)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
LOG_DIR = ROOT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "615737882760")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE = os.environ.get("AWS_PROFILE", f"AdministratorAccess-{AWS_ACCOUNT_ID}")
AWS_SSO_START_URL = os.environ.get("AWS_SSO_START_URL", "")
AWS_SSO_REGION = os.environ.get("AWS_SSO_REGION", AWS_REGION)

SSH_KEY = os.environ.get("TURBO_SSH_KEY", str(Path.home() / "turbo-notes-bastion.pem"))
SSH_USER = os.environ.get("TURBO_SSH_USER", "ec2-user")

# Staging
STAGING_SSH_HOST = os.environ.get("TURBO_STAGING_SSH_HOST", "")
STAGING_LOCAL_DB_PORT = int(os.environ.get("TURBO_STAGING_LOCAL_DB_PORT", "5435"))
STAGING_REMOTE_DB_HOST = os.environ.get("TURBO_STAGING_REMOTE_DB_HOST", "")
STAGING_REMOTE_DB_PORT = int(os.environ.get("TURBO_STAGING_REMOTE_DB_PORT", "5432"))
STAGING_DB_NAME = os.environ.get("TURBO_STAGING_DB_NAME", "turbo_notes")
STAGING_DB_USER = os.environ.get("TURBO_STAGING_DB_USER", "turbo")
STAGING_DB_PASSWORD = os.environ.get("TURBO_STAGING_DB_PASSWORD", "")

# Prod
PROD_SSH_HOST = os.environ.get("TURBO_PROD_SSH_HOST", "")
PROD_LOCAL_DB_PORT = int(os.environ.get("TURBO_PROD_LOCAL_DB_PORT", "5436"))
PROD_REMOTE_DB_HOST = os.environ.get("TURBO_PROD_REMOTE_DB_HOST", "")
PROD_REMOTE_DB_PORT = int(os.environ.get("TURBO_PROD_REMOTE_DB_PORT", "5432"))
PROD_DB_NAME = os.environ.get("TURBO_PROD_DB_NAME", "turbo_notes")
PROD_DB_USER = os.environ.get("TURBO_PROD_DB_USER", "turbo")
PROD_DB_PASSWORD = os.environ.get("TURBO_PROD_DB_PASSWORD", "")

LOG_GROUP_PREFIX = os.environ.get("TURBO_LOG_GROUP_PREFIX", "/turboai/notes")

TUNNEL_PID_FILE = LOG_DIR / "tunnel.pid"
TUNNEL_LOG_FILE = LOG_DIR / "tunnel.log"
SERVER_LOG_FILE = LOG_DIR / "server.log"

DB_QUERY_TIMEOUT_SECONDS = int(os.environ.get("DB_QUERY_TIMEOUT", "30"))
DB_MAX_ROWS = int(os.environ.get("DB_MAX_ROWS", "500"))
LOGS_MAX_EVENTS = int(os.environ.get("LOGS_MAX_EVENTS", "200"))
