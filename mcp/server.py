#!/usr/bin/env python3
"""Turbo Notes AWS MCP — staging/prod DB + CloudWatch with SSO bootstrap.

IMPORTANT: Always ask the user whether they want staging or prod before DB tools.
Prod is read-only (no db_execute; Postgres default_transaction_read_only=on).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

import config
from tools import aws_session, db_env, logs, tunnel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler(config.SERVER_LOG_FILE), logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("turbo-notes-aws-mcp")

mcp = FastMCP(
    "turbo-notes-aws",
    instructions=(
        "AWS tools for Turbo Notes (account 615737882760). "
        "ALWAYS ask whether the user wants staging or prod before DB operations. "
        "Call aws_sso_status first; if invalid, call aws_sso_login (bootstraps SSO profile). "
        "Prod DB is READ-ONLY. Seeding only on staging."
    ),
)


@mcp.tool()
def aws_sso_status() -> dict[str, Any]:
    """Check SSO session; bootstraps profile file if missing (needs AWS_SSO_START_URL)."""
    return aws_session.check_sso()


@mcp.tool()
def aws_sso_login() -> dict[str, Any]:
    """Create SSO profile if needed and run aws sso login (browser)."""
    return aws_session.sso_login_blocking()


@mcp.tool()
def choose_env_help() -> dict[str, Any]:
    """Reminder: ask the user staging vs prod before querying."""
    return {
        "message": "Ask the user: staging or prod?",
        "staging": "writable DB (with confirm), seed allowed via API",
        "prod": "READ-ONLY DB, no writes, no seed",
        "account_id": config.AWS_ACCOUNT_ID,
    }


@mcp.tool()
def tunnel_status(env: str) -> dict[str, Any]:
    """Show SSH tunnel status. env: staging|prod"""
    cfg = db_env._env_cfg(env)
    return tunnel.status_for(cfg)


@mcp.tool()
def tunnel_start(env: str, force: bool = False) -> dict[str, Any]:
    """Start SSH tunnel. env: staging|prod"""
    cfg = db_env._env_cfg(env)
    return tunnel.start(cfg, force=force)


@mcp.tool()
def tunnel_stop(env: str) -> dict[str, Any]:
    """Stop SSH tunnel. env: staging|prod"""
    cfg = db_env._env_cfg(env)
    return tunnel.stop(cfg)


@mcp.tool()
def db_ping(env: str) -> dict[str, Any]:
    """Ping Postgres. env: staging|prod — ask the user first."""
    return db_env.ping(env)


@mcp.tool()
def db_query(
    env: str,
    sql: str,
    params: list | None = None,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Read-only SQL. env: staging|prod — ask the user first."""
    return db_env.query(env, sql, params, max_rows)


@mcp.tool()
def db_execute(
    env: str,
    sql: str,
    params: list | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Mutating SQL — STAGING ONLY, confirm=true required. Refused on prod."""
    return db_env.execute(env, sql, params, confirm=confirm)


@mcp.tool()
def db_list_schemas(env: str) -> dict[str, Any]:
    """List schemas. env: staging|prod"""
    return db_env.list_schemas(env)


@mcp.tool()
def db_list_tables(env: str, schema: str | None = None) -> dict[str, Any]:
    """List tables. env: staging|prod"""
    return db_env.list_tables(env, schema)


@mcp.tool()
def db_describe_table(env: str, table: str, schema: str = "public") -> dict[str, Any]:
    """Describe table columns. env: staging|prod"""
    return db_env.describe_table(env, table, schema)


@mcp.tool()
def seed_staging(confirm: bool = False) -> dict[str, Any]:
    """Seed demo data — staging only."""
    return db_env.seed_demo("staging", confirm=confirm)


@mcp.tool()
def logs_list_groups(prefix: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List CloudWatch log groups for Turbo Notes."""
    return logs.list_groups(prefix=prefix, limit=limit)


@mcp.tool()
def logs_tail(
    log_group: str,
    minutes: int = 15,
    filter_pattern: str | None = None,
    max_events: int | None = None,
) -> dict[str, Any]:
    """Tail CloudWatch log events."""
    return logs.tail(log_group, minutes, filter_pattern, max_events)


@mcp.tool()
def logs_insights(
    log_groups: list[str] | str,
    query: str,
    minutes: int = 60,
    timeout: int = 30,
) -> dict[str, Any]:
    """CloudWatch Logs Insights query."""
    return logs.insights_query(log_groups, query, minutes, timeout)


@mcp.tool()
def env_info() -> dict[str, Any]:
    """Show MCP config (no secrets)."""
    return {
        "aws_profile": config.AWS_PROFILE,
        "aws_region": config.AWS_REGION,
        "aws_account_id": config.AWS_ACCOUNT_ID,
        "log_group_prefix": config.LOG_GROUP_PREFIX,
        "staging": {
            "ssh_host": config.STAGING_SSH_HOST,
            "local_port": config.STAGING_LOCAL_DB_PORT,
            "remote_db": config.STAGING_REMOTE_DB_HOST,
        },
        "prod": {
            "ssh_host": config.PROD_SSH_HOST,
            "local_port": config.PROD_LOCAL_DB_PORT,
            "remote_db": config.PROD_REMOTE_DB_HOST,
            "readonly": True,
        },
    }


def main() -> None:
    log.info("Starting turbo-notes AWS MCP (profile=%s)", config.AWS_PROFILE)
    mcp.run()


if __name__ == "__main__":
    main()
