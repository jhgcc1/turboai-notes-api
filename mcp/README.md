# Turbo Notes AWS MCP

MCP server for AWS account **615737882760** (`jhgcc1`): CloudWatch Logs + Postgres via bastion tunnel for **staging** and **prod**.

## Guardrails

| Env | Reads | Writes / seed |
|---|---|---|
| staging | yes | `db_execute` with `confirm=true`; seed via API |
| prod | yes | **blocked** (no writes; `default_transaction_read_only=on`) |

Always ask the user: **staging or prod?**

## SSO bootstrap

If the SSO profile is missing or expired:

1. Set `AWS_SSO_START_URL` in `mcp/.env` (or Cursor MCP env).
2. Call `aws_sso_status` / `aws_sso_login` — the MCP writes `~/.aws/config` profile `AdministratorAccess-615737882760` when needed and runs `aws sso login`.
3. Or: `../scripts/aws-sso-bootstrap.sh`

## Install

```bash
cd mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill Terraform outputs + SSO start URL
```

Add to `~/.cursor/mcp.json`:

```json
"turbo-notes-aws": {
  "command": "/home/jhgcc1/turboai/backend/mcp/.venv/bin/python",
  "args": ["/home/jhgcc1/turboai/backend/mcp/server.py"],
  "env": {
    "AWS_SSO_START_URL": "https://YOUR_ORG.awsapps.com/start",
    "AWS_ACCOUNT_ID": "615737882760",
    "AWS_REGION": "us-east-1"
  }
}
```

Or use launcher: `scripts/install.sh`.

## Tools

- `aws_sso_status`, `aws_sso_login`, `choose_env_help`, `env_info`
- `tunnel_*`, `db_ping`, `db_query`, `db_execute` (staging only), `db_list_*`, `db_describe_table`
- `seed_staging`, `logs_list_groups`, `logs_tail`, `logs_insights`
