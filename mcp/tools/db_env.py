"""Environment-aware DB access with prod read-only guardrails."""

from __future__ import annotations

import datetime
import decimal
import re
from typing import Any

import psycopg2
import psycopg2.extras

import config
from tools import tunnel

_READ_ONLY_KEYWORDS = ("select", "with", "show", "explain", "values", "table")
_FORBIDDEN_TOKENS = (
    "insert", "update", "delete", "merge", "upsert",
    "drop", "alter", "create", "truncate", "rename",
    "grant", "revoke", "reindex", "vacuum", "analyze",
    "cluster", "lock", "comment", "copy", "call", "do",
    "set ", "reset ", "refresh materialized", "discard",
    "listen", "notify", "unlisten",
)
_TOKEN_RE = re.compile(r"[a-zA-Z_]+")


def _env_cfg(env: str) -> dict[str, Any]:
    env = env.lower().strip()
    if env not in ("staging", "prod"):
        raise ValueError("env must be 'staging' or 'prod' — always ask the user which one")
    if env == "staging":
        return {
            "env_label": "STAGING",
            "local_port": config.STAGING_LOCAL_DB_PORT,
            "remote_host": config.STAGING_REMOTE_DB_HOST,
            "remote_port": config.STAGING_REMOTE_DB_PORT,
            "ssh_host": config.STAGING_SSH_HOST,
            "db_name": config.STAGING_DB_NAME,
            "db_user": config.STAGING_DB_USER,
            "db_password": config.STAGING_DB_PASSWORD,
            "readonly": False,
        }
    return {
        "env_label": "PROD",
        "local_port": config.PROD_LOCAL_DB_PORT,
        "remote_host": config.PROD_REMOTE_DB_HOST,
        "remote_port": config.PROD_REMOTE_DB_PORT,
        "ssh_host": config.PROD_SSH_HOST,
        "db_name": config.PROD_DB_NAME,
        "db_user": config.PROD_DB_USER,
        "db_password": config.PROD_DB_PASSWORD,
        "readonly": True,
    }


def _assert_read_only_sql(sql: str) -> None:
    normalized = sql.strip().lower()
    if not normalized.startswith(_READ_ONLY_KEYWORDS):
        raise ValueError("Only read-only SQL is allowed here")
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(normalized)}
    for bad in _FORBIDDEN_TOKENS:
        if bad.strip() in tokens or bad in normalized:
            if bad.strip() in ("set", "reset") and not normalized.startswith(("set ", "reset ")):
                # allow column names containing letters only via token set carefully
                continue
            if bad in ("insert", "update", "delete", "drop", "alter", "create", "truncate",
                       "grant", "revoke", "copy", "call", "do", "merge"):
                if bad in tokens:
                    raise ValueError(f"Forbidden token in SQL: {bad}")


def _connect(env: str):
    cfg = _env_cfg(env)
    if not cfg["ssh_host"] or not cfg["remote_host"]:
        raise RuntimeError(
            f"{cfg['env_label']} tunnel/DB hosts not configured. "
            "Set TURBO_* env vars from Terraform outputs (see mcp/.env.example)."
        )
    tunnel.ensure_running(cfg)
    options = f"-c statement_timeout={config.DB_QUERY_TIMEOUT_SECONDS * 1000}"
    if cfg["readonly"]:
        options += " -c default_transaction_read_only=on"
    conn = psycopg2.connect(
        host="127.0.0.1",
        port=cfg["local_port"],
        dbname=cfg["db_name"],
        user=cfg["db_user"],
        password=cfg["db_password"],
        connect_timeout=10,
        options=options,
    )
    conn.autocommit = False
    return conn, cfg


def _coerce(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value) if value % 1 else int(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode(errors="replace")
    return value


def ping(env: str) -> dict[str, Any]:
    conn, cfg = _connect(env)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"ok": True, "env_label": cfg["env_label"]}
    finally:
        conn.close()


def query(env: str, sql: str, params: list | None = None, max_rows: int | None = None) -> dict[str, Any]:
    _assert_read_only_sql(sql)
    conn, cfg = _connect(env)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or [])
            rows = cur.fetchmany(max_rows or config.DB_MAX_ROWS)
            return {
                "env_label": cfg["env_label"],
                "row_count": len(rows),
                "rows": [{k: _coerce(v) for k, v in dict(r).items()} for r in rows],
            }
    finally:
        conn.close()


def execute(env: str, sql: str, params: list | None = None, confirm: bool = False) -> dict[str, Any]:
    cfg_preview = _env_cfg(env)
    if cfg_preview["readonly"]:
        return {
            "ok": False,
            "env_label": "PROD",
            "error": "Write operations are forbidden on prod. Use staging.",
        }
    if not confirm:
        return {"ok": False, "env_label": "STAGING", "error": "Pass confirm=true to execute writes"}
    conn, cfg = _connect(env)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            affected = cur.rowcount
        conn.commit()
        return {"ok": True, "env_label": cfg["env_label"], "rowcount": affected}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_schemas(env: str) -> dict[str, Any]:
    return query(
        env,
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT IN ('pg_catalog','information_schema') ORDER BY 1",
    )


def list_tables(env: str, schema: str | None = None) -> dict[str, Any]:
    if schema:
        return query(
            env,
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema=%s ORDER BY 1,2",
            [schema],
        )
    return query(
        env,
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2",
    )


def describe_table(env: str, table: str, schema: str = "public") -> dict[str, Any]:
    return query(
        env,
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        [schema, table],
    )


def seed_demo(env: str, confirm: bool = False) -> dict[str, Any]:
    if env.lower() != "staging":
        return {"ok": False, "error": "Seeding is only allowed in staging", "env_label": "PROD"}
    if not confirm:
        return {"ok": False, "error": "Pass confirm=true to seed staging"}
    # Prefer API seed endpoint when available; SQL fallback is intentionally not implemented
    # to avoid bypassing app logic — use staging API /api/seed/ instead.
    return {
        "ok": False,
        "env_label": "STAGING",
        "error": "Use authenticated POST /api/seed/ on the staging API (or db_execute with confirm).",
        "hint": "curl -X POST \"$STAGING_API/api/seed/\" -H 'Cookie: ...'",
    }
