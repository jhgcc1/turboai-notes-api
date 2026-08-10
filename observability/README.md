# Error-triage Lambda

Turns a CloudWatch alarm into a debugged incident report in an inbox: samples
the ERROR logs behind the alarm, pulls the source code the traceback points at,
asks MiniMax for a root cause and a patch, creates a Jira issue (KAN on
staging), and emails the result.

**MiniMax runs only here.** The Django API and Next.js SPA never call an LLM;
they only emit structured ERROR logs (FE via `POST /api/observability/client-error/`).

Full design notes: [`docs/architecture/observability.md`](../docs/architecture/observability.md).

## Layout

```
handler.py            Lambda entrypoint (handler.main)
triage/settings.py    Env vars + Secrets Manager
triage/logs.py        CloudWatch Logs Insights sampling and grouping
triage/dedup.py       DynamoDB fingerprint counters
triage/repo.py        Traceback -> source code excerpts
triage/llm.py         MiniMax chat completions + defensive JSON parsing
triage/notify.py      Report rendering, SNS/SES delivery
triage/httpjson.py    urllib JSON POST helper
tests/                Unit tests (no network, no AWS)
```

## No dependencies

Only the standard library and `boto3`, which the Python 3.12 Lambda runtime
provides. That keeps the artifact around 43 KB, removes the need for a layer or
a build pipeline, and lets Terraform `archive_file` assemble the zip straight
from the working tree.

`boto3` is imported lazily inside the functions that need it so the unit tests
neither require credentials nor touch AWS.

## The bundled repository copy

Terraform adds `apps/` and `config/` to the zip under `repo/`. `REPO_ROOT`
defaults to that directory. Without it the Lambda could only forward log text;
with it the model reasons over the lines that actually raised.

## Tests

Run from the repository root with the rest of the suite:

```bash
pytest
```

`tests/conftest.py` puts this directory on `sys.path` so imports read
`triage.logs`, matching how they resolve inside the zip.
