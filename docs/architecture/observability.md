# Observability and automated error triage

How a production error becomes a debugged report in an inbox, without anyone
watching a dashboard.

## Why this exists

Before this, the only error signal was a single CloudWatch alarm on ALB 5xx
counts with a threshold of 50 in five minutes, wired to nothing. Nobody was
notified, and application-level failures that never reached a 5xx (a permission
bug returning 403, a serializer rejecting valid input, a token refresh loop)
produced no log line at all: DRF turns exceptions into responses silently.

## Flow

```
Browser (static Next.js export)                 ECS Fargate (Django)
  window.onerror / unhandledrejection             DRF exception handler
  React ErrorBoundary                             + process_exception
  reportUnexpected (5xx / programmer errors)      JSON ERROR logs (stdout)
           |                                                |
           | POST /api/observability/client-error/           |
           v                                                v
        Django logs structured JSON ERROR  -----------------+
        (stack promoted to exc_info; no LLM here)
                            |
                            | CloudWatch Logs  /turboai/notes/<env>/api
                            | metric filter  { $.level = "ERROR" }
                            v
             custom metric  AppErrorCount → alarm → SNS → triage Lambda
                            |
                            | MiniMax ONLY inside this Lambda → Jira KAN
                            v
                     SNS triage-reports → email (+ Jira browse link)
```

**Hard constraint:** MiniMax (and any LLM API) runs solely in
`turboai-notes-*-triage`. Django views and the Next.js SPA never call an LLM;
they only emit structured ERROR logs. `LLM_SECRET_ID` is granted only to the
triage Lambda IAM role (not the ECS task role).

### Lambda pipeline — where each thing happens

Inside `handler.process_alarm`, every step is a single function in
`observability/triage/` and runs in this order. Steps 2–4 are wrapped in
`record_event(...)` so a failure in one fingerprint does not abort the rest.

| Step | Module | What it does |
| --- | --- | --- |
| L1. Sample logs | `triage/logs.py` | Logs Insights query, restricted to ERROR lines for the failing alarm. |
| L2. Pick fingerprint | `triage/logs.py` + `config/fingerprint.py` | Group by fingerprint, choose the loudest occurrence. |
| L3. Decide notification | `triage/dedup.py` | Atomic `UpdateItem` with `ALL_OLD` returns whether this is a new fingerprint or the Nth recurrence. |
| L4. Bundle source | `triage/repo.py` | Resolve traceback frames into ±40-line numbered windows from the bundled `repo/apps/` and `repo/config/`, capped by `CODE_CONTEXT_CHARS`. |
| L5. Analyse | `triage/llm.py` | Calls MiniMax with the failing request + traceback + bundled code; returns `{severity, summary, root_cause, suspected_files, suggested_fix, labels, repro_hint, diff?}`. |
| L6. Build report | `triage/notify.py` | Render the plain-text report. |
| **J3. Create issue** | `triage/jira.py` | When `JIRA_ENABLED=true`: `POST /rest/api/3/issue` with the analysis summary, ADF description (including a **CloudWatch** Logs Insights deep link filtered by fingerprint/request_id), severity prefix, and labels. Returns the issue key, or `None` if disabled or Jira fails. Errors are logged and swallowed — never block the e-mail. |
| **D4. Attach issue** | `triage/dedup.py` | Persist the returned key on the DynamoDB item so future occurrences of the same fingerprint comment on the existing ticket instead of opening a new one. |
| **N4. Send e-mail** | `triage/notify.py` | Publish to `<name>-triage-reports` (or send via SES). Subject prefixes `[KEY]` when Jira returned a key, body gains a `Jira: <browse-url>` line, or an `Existing ticket: <key>` line when dedup found one. SNS delivers to the address from `TF_VAR_ops_email`. |

Jira and the e-mail are independent by design: a Jira outage still leaves
you with an inbox report, and a mail outage still leaves the ticket open.
The same analysis object feeds both, so what arrives in the e-mail matches
what lands in the ticket — same severity, same summary, same labels, same
suggested fix.
```

### Two topics, on purpose

Alarms fan in to `<name>-alarms`, and the Lambda publishes its report to
`<name>-triage-reports`. With a single topic the function would subscribe to
its own output and re-trigger itself indefinitely.

Infrastructure alarms that the Lambda cannot debug from application logs
(no healthy hosts, RDS CPU and storage, triage Lambda failures) publish
straight to the reports topic so they reach the operator without a round trip.

## Structured logging

Every line is a single JSON object (`config/middleware.py:JsonFormatter`)
carrying `level`, `logger`, `message`, `time`, `service`, `environment`, plus
whatever the call site passed as `extra`: `request_id`, `route`, `method`,
`status`, `duration_ms`, `user_id`, `error_type`, `fingerprint`.

ERROR lines are additionally prefixed with `[ERROR]` in the message so they
stay greppable in plain-text views (ECS console, `docker logs`, the MCP
`logs_tail` tool).

**The metric filter matches `{ $.level = "ERROR" }`, not the literal string
`[ERROR]`.** A note whose body contains `[ERROR]` would otherwise trigger a
false alarm and burn an LLM call on a non-incident.

### Where errors are captured

| Source | Level | Notes |
| --- | --- | --- |
| `config/exception_handler.py` | ERROR for 5xx, WARNING for 4xx | Every DRF exception, with `exc_info` and a fingerprint; sets `_error_logged` on 5xx |
| `config/middleware.py` `process_exception` | ERROR | Uncaught non-DRF exceptions with full traceback + fingerprint |
| `config/middleware.py` `process_response` | ERROR | Status >= 500 only when not already logged (avoids traceback-less duplicates) |
| `apps/accounts/views.py` | WARNING | Token blacklist and refresh rejections that were previously swallowed |
| `apps/observability/views.py` | ERROR | Browser client errors POSTed to `/api/observability/client-error/` (size-capped, rate-limited; CSRF enforced when cookie-authed). JS `stack` is promoted to JSON `exc_info` by `JsonFormatter` so Logs Insights always samples a traceback. |

4xx stays at WARNING deliberately. Logging ordinary client mistakes at ERROR would make
the alarm fire on bad requests. Browser runtime failures are different: the SPA is a
static export and cannot talk to CloudWatch, so it POSTs to the API, which emits a
single structured ERROR line and reuses the same CloudWatch → Lambda → MiniMax → Jira
pipeline. FE stacks are often minified; React `componentStack` (`source=boundary`) is
the richest client frame data. Expected auth/validation failures (401/400/…) are not
reported from the SPA catch paths — only unexpected 5xx / programmer errors.

### One shipper, not two

`watchtower` used to run alongside the ECS `awslogs` driver, sending every line
to the same log group twice: double ingest cost and double counting on the
metric filter. The task definition now sets `CLOUDWATCH_ENABLED=false` and
relies on the driver. Turn watchtower back on only for runtimes that do not
forward stdout, such as a plain EC2 process.

## Fingerprints and deduplication

`config/fingerprint.py` hashes the exception type, the normalised route and the
deepest non-vendor traceback frame into a 16-character key. Identifiers in the
path are collapsed (`/api/notes/42/` and `/api/notes/43/` share a route), and
the exception *message* is excluded because it usually embeds ids or emails
that differ between occurrences of the same bug. When no exception is raised
(as on the 4xx path through the DRF handler), the fingerprint falls back to
`(route, status_code)` so equivalent client errors still coalesce.

State lives in DynamoDB (`<name>-error-fingerprints`), keyed by fingerprint,
with a TTL attribute. A single `UpdateItem` with `ALL_OLD` both counts the
occurrence and reveals whether it is the first, which avoids the read-then-write
race that would let two concurrent alarms both decide they are first.

### Notification policy

- **First sighting** of a fingerprint: send a report.
- **Afterwards**: send only every `RESEND_EVERY` occurrences (default 10).
- **After `DEDUP_TTL_HOURS`** (default 72) the record expires, so a bug that
  goes quiet and comes back is treated as new again.

Without this, an alarm that stays in ALARM state emails on every evaluation
period.

## Reading the source code to debug

This is the part that makes the report useful rather than a log forward.

The Lambda zip carries a copy of `apps/` and `config/` under `repo/`, assembled
by Terraform's `archive_file` from the working tree — no build step, no layer,
about 43 KB zipped. At runtime `triage/repo.py`:

1. Parses traceback frames, discarding `site-packages` and stdlib frames.
2. Resolves each frame's container path (`/app/apps/notes/views.py`) to a
   bundled file by matching progressively shorter path suffixes.
3. Reads a line-numbered window around the failing line (±40 lines by default).
4. Orders excerpts deepest-frame-first, since that is where the exception
   actually surfaced.

The whole repository is never sent. Selection is traceback-driven and capped by
a total character budget (`CODE_CONTEXT_CHARS`, default 24000); when no project
frame can be resolved, it falls back to mapping the failing route to an app
directory (`/api/notes/` to `apps/notes/{views,serializers,services,models,
permissions}.py`).

The model receives: the failing request, the traceback, a file index of the
repository, the resolved source excerpts, and a sample of other recent
occurrences. It is asked for a root cause grounded in the code shown, suspected
file:line locations, a unified diff, fix steps, repro steps, severity and a
confidence rating.

## Degradation

The pipeline is built so a report always goes out:

- LLM transport error, empty response, or unparsable JSON produces a `degraded`
  analysis carrying the raw output, and the email is still sent with the logs.
- The model's response is parsed by locating the first balanced JSON object,
  after stripping `<think>` blocks, because reasoning models wrap answers in
  prose even when told not to.
- SES failure falls back to SNS.
- One failing alarm in a batch does not stop the others.

## Alarms

| Alarm | Metric | Default threshold | Routes to |
| --- | --- | --- | --- |
| `-app-errors` | `AppErrorCount` (log filter) | >= 5 / 5 min (prod: 3) | triage Lambda → email (+ Jira, when `JIRA_ENABLED=true`) |
| `-5xx` | `HTTPCode_Target_5XX_Count` | > 10 / 5 min (prod: 5) | triage Lambda → email (+ Jira, when `JIRA_ENABLED=true`) |
| `-latency-p95` | `TargetResponseTime` p95 | > 2 s for 2 periods | triage Lambda → email |
| `-no-healthy-hosts` | `HealthyHostCount` | < 1 for 3 min | email |
| `-rds-cpu` | RDS `CPUUtilization` | > 85% for 15 min | email |
| `-rds-free-storage` | RDS `FreeStorageSpace` | < 2 GiB | email |
| `-triage-failures` | Lambda `Errors` | >= 1 | email |

`HealthyHostCount` is used instead of ECS `RunningTaskCount` because the latter
requires Container Insights, which is not enabled.

A dashboard (`<name>-observability`) shows error counts, response classes,
latency percentiles, database load, and a live table of recent errors.

## Operating it

### One-time setup after `terraform apply`

1. **Confirm the email subscription.** Set `ops_email` (via `TF_VAR_ops_email`
   or the env `main.tf`) and click the confirmation link AWS sends. Until then
   nothing is delivered.

2. **Populate the MiniMax key.** Terraform creates the secret with a
   `REPLACE_ME` placeholder and `ignore_changes` on its value, so the real key
   never enters a tfvars file, the state file, or version control:

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id "$(terraform output -raw llm_secret_arn)" \
     --secret-string '{"api_key":"sk-...","base_url":"https://api.minimax.io/v1","model":"MiniMax-M2.1"}'
   ```

   > **Rotate the current key.** The key used while building this was pasted
   > into a chat window and must be considered compromised. Issue a new one at
   > <https://platform.minimax.io> and never commit a key to this repository.

3. **Populate the Jira secret** (when `jira_enabled = true`). Same out-of-band
   pattern; the token never enters the state file. Staging is already enabled
   (`jira_enabled=true`, project `OPS` on `https://joaocavalcanti002.atlassian.net`)
   with `turboai-notes-staging-jira-credentials` populated — see
   `docs/process/17-observability-staging-apply.md`.

   ```bash
   aws secretsmanager put-secret-value \
     --secret-id "$(terraform output -raw jira_secret_arn)" \
     --secret-string '{
       "base_url":  "https://your-site.atlassian.net",
       "email":     "ops@example.com",
       "api_token": "ATATT3xFfGF0...your token here..."
     }'
   ```

### Verifying it end to end

Invoke the function directly; it treats a bare payload as an alarm:

```bash
aws lambda invoke \
  --function-name "$(terraform output -raw triage_function_name)" \
  --payload '{"AlarmName":"manual-test","NewStateValue":"ALARM"}' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

Set `DRY_RUN=true` on the function to exercise the whole pipeline, including
the LLM call, while writing the report to its own logs instead of emailing it.

### Tuning

All knobs are Lambda environment variables, surfaced as module variables so
operators can adjust them per environment without touching code:

| Variable | Default | What it does |
| --- | --- | --- |
| `LOOKBACK_MINUTES` | 15 | How far back the Lambda samples ERROR logs when an alarm fires. |
| `MAX_EVENTS` | 25 | Maximum ERROR lines fed to the LLM per invocation. |
| `RESEND_EVERY` | 10 | After the first report, re-notify every N occurrences of a fingerprint. |
| `DEDUP_TTL_HOURS` | 72 | How long a fingerprint stays suppressed before counting as new again. |
| `CODE_WINDOW_LINES` | 40 | Lines of context fetched above and below each traceback frame. |
| `CODE_CONTEXT_CHARS` | 24000 | Hard cap on the total size of source code shipped to the LLM. |
| `LLM_MODEL` | `MiniMax-M2.1` | Model id used for analysis. |
| `JIRA_ENABLED` | `false` | When `true`, the Lambda creates a Jira issue for the first sighting of a fingerprint and links the ticket in the email. Disabled envs never touch the Jira API. |
| `JIRA_BASE_URL` | (empty) | Reserved for the future — the actual base URL is read from the secret so it never sits in `lambda:GetFunctionConfiguration`. Must remain empty. |
| `JIRA_PROJECT_KEY` | (empty) | Jira project key (e.g. `KAN`, `OPS`). Required when `JIRA_ENABLED=true`. |
| `JIRA_ISSUE_TYPE` | `Bug` | Jira issue type created by the Lambda (`Task`, `Story`, `Incident` all work). |
| `DRY_RUN` | `false` | When `true`, run the full pipeline but write the report to logs instead of sending email. |

### Cost

Within the AWS free tier at this traffic level: the metric filter and alarms
are free up to 10 alarms, Lambda invocations are negligible, DynamoDB is
on-demand, and SNS email is free up to 1,000 notifications. The variable cost
is the LLM call, roughly a few cents per distinct error thanks to
fingerprint deduplication.

## Jira

The Lambda can optionally create a Jira Cloud issue for the **first** sighting
of a fingerprint, and the email that goes out always links to the ticket. The
path:

```
analyse -> create_issue (POST /rest/api/3/issue) -> dedup.attach_issue
   -> build_report(subject=[OPS-42] ..., body "Jira: https://.../browse/OPS-42")
   -> SNS / SES
```

The next time the same fingerprint shows up, the `issue_key` already lives in
the DynamoDB record so the Lambda skips the create call and the email starts
with `Existing ticket: OPS-42` (or, when the operator simply files the
follow-up under the existing key, the report body points to the same URL).
This is the "comment on existing ticket" behaviour described in the design
notes — there is no separate `POST .../comment` today, the email is the
follow-up channel.

The Jira call is best-effort: a transport error, 4xx/5xx, malformed secret
or disabled env all collapse to `issue_key = None` and the email is still
sent. The whole Jira branch is wrapped so a broken integration cannot block
the alarm-to-email path.

### Variables and credentials

| Terraform variable | Lambda env var | Default | Required when `JIRA_ENABLED=true`? |
| --- | --- | --- | --- |
| `jira_enabled` | `JIRA_ENABLED` | `false` | — |
| `jira_project_key` | `JIRA_PROJECT_KEY` | empty | yes |
| `jira_issue_type` | `JIRA_ISSUE_TYPE` | `Bug` | no |
| `jira_secret_id_override` | `JIRA_SECRET_ID` | (uses module-created secret) | optional |

The email, the API token and the base URL **never** enter Terraform, the
state file, or the Lambda environment. The module creates a Secrets Manager
placeholder named `turboai-notes-{env}-jira-credentials` with `ignore_changes`
on `secret_string` — the same out-of-band pattern as the LLM secret. The
operator populates it once with `aws secretsmanager put-secret-value`:

```bash
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw jira_secret_arn)" \
  --secret-string '{
    "base_url":  "https://your-site.atlassian.net",
    "email":     "ops@example.com",
    "api_token": "ATATT3xFfGF0...your token here..."
  }'
```

The Lambda reads it via `secretsmanager:GetSecretValue` (the role gets a
statement scoped to the specific ARN when `jira_enabled` is true) and
caches the result for the life of the container — same pattern as
`LLM_SECRET_ID`. The HTTP Basic auth header is built per request and is
never logged.

### Format of the issue

- **Project**: from `JIRA_PROJECT_KEY`.
- **Issue type**: from `JIRA_ISSUE_TYPE`, default `Bug`.
- **Summary**: `[{SEVERITY}] {analysis.summary}` (truncated to 255 chars).
- **Labels**: `analysis.labels`, de-duplicated and capped at 10.
- **Description**: Atlassian Document Format (ADF), **not** markdown.
  `Root cause`, `Suspected locations`, `Fix steps`, `Reproduction` and
  the proposed patch (as a `diff` code block) are emitted as ADF
  paragraphs / headings / bullet lists. The self-link to the ticket is
  rendered as an ADF link node, not as a URL string.

The `self` field can only be filled in **after** the create call returns
the key, so the description is sent without the link and the email is
what carries it.
