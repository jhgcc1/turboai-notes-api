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
ECS Fargate task (Django + gunicorn)
  stdout -> awslogs driver -> CloudWatch Logs  /turboai/notes/<env>/api
                                    |
                                    | metric filter  { $.level = "ERROR" }
                                    v
                     custom metric  turboai/notes/<env>  AppErrorCount
                                    |
                                    | alarm: >= N errors in 5 min
                                    v
                          SNS topic  <name>-alarms
                                    |
                                    v
                    Lambda  <name>-triage  (python3.12, stdlib + boto3)
                      1. Logs Insights: sample recent ERROR rows
                      2. group by fingerprint, pick the loudest
                      3. DynamoDB: count occurrences, decide if new
                      4. resolve traceback frames to bundled source code
                      5. MiniMax: root cause + proposed patch
                      6. render plain-text report
                                    |
                                    v
                     SNS topic  <name>-triage-reports  -> email
                                (or SES when configured)
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
| `config/exception_handler.py` | ERROR for 5xx, WARNING for 4xx | Every DRF exception, with `exc_info` and a fingerprint |
| `config/middleware.py` | ERROR | Any response with status >= 500 |
| `apps/accounts/views.py` | WARNING | Token blacklist and refresh rejections that were previously swallowed |

4xx stays at WARNING deliberately. Logging client mistakes at ERROR would make
the alarm fire on ordinary bad requests.

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
| `-app-errors` | `AppErrorCount` (log filter) | >= 5 / 5 min (prod: 3) | triage Lambda |
| `-5xx` | `HTTPCode_Target_5XX_Count` | > 10 / 5 min (prod: 5) | triage Lambda |
| `-latency-p95` | `TargetResponseTime` p95 | > 2 s for 2 periods | triage Lambda |
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
| `DRY_RUN` | `false` | When `true`, run the full pipeline but write the report to logs instead of sending email. |

### Cost

Within the AWS free tier at this traffic level: the metric filter and alarms
are free up to 10 alarms, Lambda invocations are negligible, DynamoDB is
on-demand, and SNS email is free up to 1,000 notifications. The variable cost
is the LLM call, roughly a few cents per distinct error thanks to
fingerprint deduplication.

## Adding Jira later

Ticket creation was dropped because the Atlassian account has no Jira site
("Supported sites required" during OAuth). The pieces that make it easy to add
are already in place: a stable fingerprint, a DynamoDB record to store the
issue key against, and a structured `Analysis` object.

To wire it up, create a free Jira Cloud site and a project, add a Secrets
Manager secret holding `base_url`, `email` and `api_token`, then post the
analysis to `POST /rest/api/3/issue` (the description field takes Atlassian
Document Format, not markdown) from a new branch in
`handler.process_alarm` next to `send_report`. Use `dedup.attach_issue` to
record the returned key so recurrences comment on the existing ticket instead
of opening a new one.
