# Turbo Notes API

Django + Django REST Framework backend for the Turbo AI notes-taking hiring challenge.

## Stack

- Django 5 / DRF / SimpleJWT (httpOnly cookies)
- PostgreSQL 16
- drf-spectacular (Swagger at `/api/docs/`)
- Docker Compose (API + DB + frontend)

## AI tools used

Built end-to-end in **Cursor**. The primary interactive planning/coordination session ran on **Grok 4.5 High Fast** (the model set at kickoff), which turned the hiring-challenge brief into the locked plan in `docs/process/00-MASTER-PLAN.md` and did the initial scaffolding. The bulk of the implementation, infra, and fix-up work — Django/DRF + notes API, Terraform for AWS (VPC/ECS/RDS/S3/CloudFront/IAM/CloudWatch), GitHub CI/CD, AWS SSO/IAM/OIDC bootstrap, staging-deploy debugging, a security audit (secrets, CORS/CSRF), Figma/e2e verification, and this documentation — was delegated to dozens of asynchronous Cursor "Multitask Mode" subagents, which inherited the session's configured model (a mix of Grok 4.5 High Fast and Claude Sonnet 5 across this multi-day session). See [`docs/process/13-ai-development-process.md`](../docs/process/13-ai-development-process.md) for the full writeup, including the verbatim original prompt and a clearly-labeled, methodology-based token/cost estimate.

## Demo video

~5 minute walkthrough (English): [turbo-notes-demo.mp4](https://d1qdib1mcwro0s.cloudfront.net/demo/turbo-notes-demo.mp4)

Hosted on staging S3/CloudFront (`turboai-notes-staging-web-615737882760`); opens directly in the browser with no login.

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
python manage.py makemigrations --check --dry-run   # CI also runs this
```

### Migrations (CI check vs deploy apply)

| When | What | Where |
|------|------|--------|
| **CI** (every PR/push) | `makemigrations --check --dry-run` — fails if model changes lack a committed migration | `.github/workflows/ci.yml` (SQLite; never touches RDS) |
| **Deploy** (staging + prod) | `migrate --noinput` on every new ECS task start | `scripts/entrypoint.sh` (Docker `ENTRYPOINT`) after force-new-deployment |

CI does **not** apply migrations to staging/prod. Apply is defense-in-depth on container boot so each rolled task brings the env’s RDS schema forward; we do not also run a separate ECS one-off migrate in `deploy.yml` (avoids double-migrate races).

## Auth

Register/login set `access_token` + `refresh_token` httpOnly cookies (`SameSite=Lax; Secure` in staging/prod). All note/category queries are scoped to the authenticated user.

Staging/prod browsers should call the API on the **web** CloudFront host (`/api/*` → ALB) so cookies are first-party (Incognito-safe). The separate API CloudFront distribution remains for Swagger/direct access.

## Observability

JSON logs, CloudWatch alarms, and a Lambda that debugs errors against the
source code and emails a report with a proposed fix. See
[`docs/architecture/observability.md`](docs/architecture/observability.md).

## AWS MCP

See [`mcp/README.md`](mcp/README.md) for staging/prod CloudWatch + Postgres tools (prod is read-only).
