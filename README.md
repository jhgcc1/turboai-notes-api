# Turbo Notes API

Django + Django REST Framework backend for the Turbo AI notes-taking hiring challenge.

## Stack

- Django 5 / DRF / SimpleJWT (httpOnly cookies)
- PostgreSQL 16
- drf-spectacular (Swagger at `/api/docs/`)
- Docker Compose (API + DB + frontend)

## AI tools used

Built with **Cursor Grok 4.5 High Fast** for scaffolding, tests, Terraform, CI, and AWS MCP patterned on an existing employee-lifecycle MCP.

## Quick start (local)

```bash
cp .env.example .env
docker compose up --build
```

API: http://localhost:8000 — Swagger: http://localhost:8000/api/docs/

### Without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export USE_SQLITE=true SECRET_KEY=dev
python manage.py migrate
python manage.py runserver
```

## Tests / quality

```bash
pytest          # 100% coverage gate on apps/
ruff check .
ruff format --check .
mypy apps config
```

## Auth

Register/login set `access_token` + `refresh_token` httpOnly cookies. All note/category queries are scoped to the authenticated user.

## AWS MCP

See [`mcp/README.md`](mcp/README.md) for staging/prod CloudWatch + Postgres tools (prod is read-only).
