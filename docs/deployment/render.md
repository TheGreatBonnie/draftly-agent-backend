# Deploy Draftly on Render

Deploy the API and RQ worker from the same hardened Docker image. Use Render
Key Value for queues and event streams, and keep Neon PostgreSQL as the durable
database.

## Architecture

| Component | Service |
|---|---|
| FastAPI | Render Web Service (`draftly-api`) |
| RQ worker | Render Background Worker (`draftly-worker`) |
| Redis-compatible queue and event bus | Render Key Value (`draftly-kv`) |
| PostgreSQL | Existing Neon database |
| Next.js UI | Vercel |

The committed `render.yaml` deploys the `render-deployment` branch in Oregon.
Keep that branch dedicated to Render releases; merge or promote application
changes into it when they are ready to deploy.

## 1. Validate the deployment artifacts

Build the same image Render will use:

```bash
docker buildx build \
  --platform linux/amd64 \
  -f docker/Dockerfile.render \
  -t draftly/backend:render \
  --load .
```

The image runs as a non-root user, includes `git`, the RQ worker, bundled
skills, SQL migrations, and `scripts/bootstrap.py`, and excludes local `.env`
and `secrets/` files.

The installed Render CLI might not provide Blueprint validation. The Blueprint
schema is available at `https://render.com/schema/render.yaml.json` for editor
or CI validation.

## 2. Create the Blueprint

In Render, choose **New → Blueprint**, connect
`TheGreatBonnie/draftly-agent-backend`, select the `render-deployment` branch,
and use the repository-root `render.yaml`.

The Blueprint creates:

- a paid `draftly-api` Docker web service;
- a paid `draftly-worker` Docker background worker; and
- a paid, internal-only `draftly-kv` instance with `noeviction` and
  journal-plus-snapshot persistence.

The API alone runs `python scripts/bootstrap.py` before every deployment. Do
not add that command to the worker. Render probes `/health`; use
`/health/ready` for dependency monitoring rather than deployment liveness.

## 3. Configure shared backend values

Set these on both the API and worker:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon pooled PostgreSQL URL |
| `REDIS_URL` | Populated by the Blueprint from `draftly-kv` |
| `ENVIRONMENT` | `production` |
| `RQ_ENABLED` | `true` |
| `STRANDS_SESSION_STORAGE` | `database` |
| `DRAFTLY_ENABLED_PROVIDERS` | Comma-separated providers enabled for this environment |
| Provider API keys | Only the keys for enabled providers |
| `GITHUB_APP_ID` | GitHub App ID |
| `GITHUB_APP_SLUG` | GitHub App slug |
| `GITHUB_PRIVATE_KEY_PATH` | `/etc/secrets/github-app-private-key.pem` |

Upload `github-app-private-key.pem` under **Environment → Secret Files** on
both services. Never copy the local `secrets/` directory into an image.

Add Slack, Discord, Tavily, SendGrid, and other credentials only when the
corresponding integration is enabled. The API needs inbound webhook and OAuth
credentials; the worker needs the outbound credentials used by its jobs.

## 4. Configure API-only values

| Variable | Value |
|---|---|
| `FRONTEND_URL` | Production Vercel origin |
| `ALLOWED_ORIGINS` | Exact production Vercel origin, comma-separated if needed |
| `PUBLIC_API_URL` | Public Render API origin |
| `APP_URL` | Public Render API origin |
| `REVIEW_DASHBOARD_URL` | Production Vercel review URL |
| `CLERK_PUBLISHABLE_KEY` | Clerk publishable key |
| `CLERK_SECRET_KEY` | Clerk backend secret, required by member/reviewer operations |
| `CLERK_SIGNING_SECRET` | Clerk webhook signing secret |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub App installation OAuth |
| `GITHUB_WEBHOOK_SECRET` | GitHub webhook HMAC secret |

Do not set `PYTHON_VERSION`; Docker pins the Python minor version in the image.

## 5. Configure Vercel

Set the UI production environment:

```env
API_URL=https://draftly-api.onrender.com
NEXT_PUBLIC_API_URL=https://draftly-api.onrender.com
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<publishable-key>
CLERK_SECRET_KEY=<secret-key>
```

`API_URL` powers the same-origin Next.js `/api` rewrite. The short dashboard
ticket POST stays on that rewrite. `NEXT_PUBLIC_API_URL` sends only the
long-lived EventSource connection directly to Render. It is public build-time
configuration, so changing it requires a Vercel redeploy.

For previews, use an isolated staging API or leave `NEXT_PUBLIC_API_URL` unset
to retain the same-origin path. Never point preview builds at production data.

## 6. Verify production

1. Confirm the API pre-deploy migration succeeds.
2. Confirm `GET /health` returns `200` and `/health/ready` reports healthy
   database, memory, and evaluation dependencies.
3. Confirm the worker registers the `scheduled`, `webhooks`, and `default`
   queues and connects to the same internal Key Value URL as the API.
4. Start a workflow and confirm the worker consumes it.
5. In browser developer tools, confirm normal `/api` requests use Vercel and
   dashboard SSE requests use the Render hostname with
   `Content-Type: text/event-stream`.
6. Exercise a GitHub App operation to confirm both services can read the
   uploaded private key.

To roll back, select the previous Render deployment. To move SSE back through
Vercel, remove `NEXT_PUBLIC_API_URL` and redeploy the UI.

## 7. Free-tier deployment (no payment card)

Sections 1-6 cover the paid Blueprint. Render's Background Worker has **no free
tier**, so that path cannot be used without a card. The free path keeps the API
and queue on Render's free tier and moves both the RQ worker and migrations to
GitHub Actions.

| Component | Where | Cost |
| --- | --- | --- |
| FastAPI | Render free Web Service (`draftly-api`) | $0 |
| Queue and event bus | Render free Key Value (`draftly-kv`, Valkey 8) | $0 |
| RQ worker | GitHub Actions, burst job | $0 (public repo) |
| PostgreSQL | Neon | $0 |

Why GitHub Actions: standard GitHub-hosted runner minutes are free in public
repositories, and a burst worker costs minutes rather than billing
continuously by the second. Render, Railway, and Modal all either bill a
polling worker per second or offer no free worker tier.

### 7.1 Deploy the API and queue

Create a Blueprint from `render.free.yaml` instead of `render.yaml`
(New → Blueprint → select branch `render-deployment` → set the Blueprint path
to `render.free.yaml`). Set the same `sync: false` secrets as sections 3 and 4.

Five differences from the paid Blueprint:

- both services run on the `free` plan;
- `draftly-kv` opens its external connection to `0.0.0.0/0` so the Actions
  runner can reach it (GitHub's egress ranges are too large and too shifting
  to allowlist individually);
- there is no `draftly-worker` service;
- **there is no `preDeployCommand`.** Render states the pre-deploy command is
  "available for paid web services, private services, and background workers",
  so a free web service silently ignores it. Migrations must run from the
  `migrate` workflow instead — see 7.4. This is the single easiest mistake to
  make here: without it the API boots against an unmigrated schema and fails
  with a generic database error; and
- **there is no `maxShutdownDelaySeconds`.** Render rejects the Blueprint with
  `max shutdown delay is not supported for free tier services`. The key is
  valid in the Blueprint schema but gated by plan, so it must be omitted
  entirely rather than set to a smaller value. See 7.7 for the consequence.

Note that Render gates some keys by compute plan *after* schema validation, so
a Blueprint can validate cleanly and still be rejected at apply time. If one is
rejected, remove the key rather than lowering its value.

Free Key Value is **25 MB, single-instance, and in-memory only**. Queued jobs
are lost whenever the instance restarts, which Render may do at any time.

### 7.2 Render environment variables

`render.free.yaml` declares 21 environment variables in three groups, defined
by *how you supply them*. Render prompts you for every `sync: false` entry at
Blueprint creation.

#### Supplied by the Blueprint — nothing to type

| Variable | Value | Why it is fixed |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | Environment branching and the `/health/ready` dependency report |
| `LOG_LEVEL` | `INFO` | `DEBUG` on a free tier burns the finite pipeline-minute and bandwidth allowances |
| `RQ_ENABLED` | `true` | Routes dispatch through Redis. Setting it false silently falls back to in-process execution, running jobs inside the web request |
| `STRANDS_SESSION_STORAGE` | `database` | Forced by the platform: Render's filesystem is ephemeral and Actions runners are destroyed after each run, so file-backed sessions would be lost between drains |
| `GITHUB_PRIVATE_KEY_PATH` | `/etc/secrets/github-app-private-key.pem` | A **file path, not a secret**. The PEM is mounted at this path on Render and written there at runtime by the Actions workflow |
| `REDIS_URL` | `fromService` | Resolved to the internal Key Value string at deploy time. Never paste a value |

The internal `REDIS_URL` works because both services are in `oregon`. Render
documents that free web services "can't _receive_ private network traffic" but
"can _send_ private network requests to your data stores ... in the same
region".

#### Set directly

| Variable | Value |
| --- | --- |
| `DRAFTLY_ENABLED_PROVIDERS` | `mantle,mantle-openai,orcarouter,nvidia,openrouter` |
| `FRONTEND_URL` | Vercel URL, e.g. `https://your-app.vercel.app` |
| `ALLOWED_ORIGINS` | Comma-separated allowed origins; must include the Vercel URL because the dashboard SSE stream is cross-origin |
| `APP_URL` | Public API base URL |
| `PUBLIC_API_URL` | Public API base URL |
| `REVIEW_DASHBOARD_URL` | Review dashboard URL |

`FRONTEND_URL` is the OAuth redirect target for GitHub and Slack
(`routes/github.py` builds `f"{settings.frontend_url}{oauth_state['return_to']}"`),
so a wrong value sends the callback to the wrong origin.

`DRAFTLY_ENABLED_PROVIDERS` is comma-separated with **no spaces**, and the
names are hyphenated — `mantle-openai`, not `mantle_openai`. Unset means *all*
providers. An unrecognised name is dropped with a warning, which silently
narrows routing *and* failover rotation while the config still looks correct,
so verify the `enabled_providers_unknown` startup log line.

#### Secrets (`sync: false`) — 14 values to paste

| Secret | Source |
| --- | --- |
| `DATABASE_URL` | Neon connection string. Either name works; config aliases `NEON_DATABASE_URL` and `DATABASE_URL` |
| `CLERK_PUBLISHABLE_KEY` | Clerk dashboard. Required in practice: `api/auth.py` derives the JWKS domain from it and raises `AttributeError` on `None` |
| `CLERK_SECRET_KEY` | Clerk dashboard |
| `CLERK_SIGNING_SECRET` | Clerk dashboard |
| `GITHUB_APP_ID` | GitHub App settings |
| `GITHUB_APP_SLUG` | GitHub App settings |
| `GITHUB_CLIENT_ID` | GitHub App settings |
| `GITHUB_CLIENT_SECRET` | GitHub App settings |
| `GITHUB_WEBHOOK_SECRET` | GitHub App settings |
| `MANTLE_API_KEY` | Mantle — serves both `mantle` and `mantle-openai` |
| `ORCAROUTER_API_KEY` | OrcaRouter |
| `NVIDIA_API_KEY` | NVIDIA |
| `OPENROUTER_API_KEY` | OpenRouter |

Four provider keys cover five enabled providers: `mantle-openai` reuses
`MANTLE_API_KEY` and differs only by endpoint.

**Requesty is optional.** `factory.py` pins `provider="requesty"` for the three
stage models (`stage-research`, `stage-review`, `stage-rubric-grader`), and
`RESEARCH_MODEL` / `REVIEW_MODEL` / `RUBRIC_GRADER_MODEL` override only the
model *id*, never the provider. If Requesty is excluded from
`DRAFTLY_ENABLED_PROVIDERS`, `ModelRouter.resolve_model` falls back to selecting
by the model's declared capabilities, and every one is covered without it:

| Stage model | Capability | Served by |
| --- | --- | --- |
| `stage-research` | `research` | `mantle` (kimi-k2-5, kimi-k2-thinking, minimax-m2, qwen3-coder-next) |
| `stage-review` | `verification` | `orcarouter` (`review-orca`) or `mantle` |
| `stage-rubric-grader` | `evaluation` | `orcarouter` (`grader-orca`) |

So you can set `DRAFTLY_ENABLED_PROVIDERS` to exactly your five providers and
omit `REQUESTY_API_KEY` entirely. Research and grading will then run on Mantle
and OrcaRouter instead of Requesty — a behavioural change to which models grade
your output, so pick deliberately.

`GITHUB_TOKEN` is also mandatory and is easy to miss: `build_integrations()`
constructs `GitHubClient` unconditionally, and with no per-workflow
installation id set at startup the client builds `GitHubAuth()`, which raises
`GITHUB_TOKEN is not configured.` before the server binds its port.

These are needed on the **web service**, not only on the worker. The API builds
a `ModelRouter` at startup (`dependencies.build_models`), so provider
credentials must be present at deploy time. The same four values must also be
added as Actions secrets — see 7.3.

Two optional provider base URLs may be set if you use self-hosted gateways —
`MANTLE_ENDPOINT_URL`, `MANTLE_OPENAI_ENDPOINT_URL`, `ORCAROUTER_BASE_URL`,
`NVIDIA_BASE_URL`, `OPENROUTER_BASE_URL`. Each falls back to the provider's
compiled-in default when absent, so omit them unless needed.

#### Do not copy these from your local `.env`

- **`REDIS_URL`** — your `.env` holds a local value for
  `docker-compose.redis.yml`. The Blueprint wires the API side automatically;
  only the Actions worker needs the external `rediss://` URL (7.3).
- **Model-name overrides** — omit all of them. Three cannot be set on Render at
  all, because a `.` is invalid in an environment variable name:
  `ORCA_OPENAI_5.6_LUNA_MODEL`, `NVIDIA_GLM_5.2_MODEL`,
  `NVIDIA_KIMI_K2.6_MODEL`. `python-dotenv` accepts them locally while Render
  drops them, so the production model list would silently differ from
  development.
- **Tuning knobs** — `AppSettings` has **zero required fields**, so every
  remaining variable only overrides a working default. Adding one can introduce
  divergence, never satisfy a requirement.

In particular, leave `EVENTS_HEARTBEAT_SECONDS` alone: it is declared in
`config.py` and referenced nowhere else. The SSE routes use a hardcoded
15-second constant, so setting it has no effect.

### 7.3 Configure the GitHub Actions secrets

Add these under Settings → Secrets and variables → Actions:

| Secret | Source |
| --- | --- |
| `DATABASE_URL` | Neon connection string (same value as on Render) |
| `REDIS_URL` | Render `draftly-kv` **external** connection string (`rediss://...`) |
| `GITHUB_APP_ID` | GitHub App settings |
| `GITHUB_APP_SLUG` | GitHub App settings |
| `GITHUB_APP_PRIVATE_KEY` | Full PEM including `BEGIN`/`END` lines |
| `MANTLE_API_KEY` | Mantle — serves both `mantle` and `mantle-openai` |
| `ORCAROUTER_API_KEY` | OrcaRouter |
| `NVIDIA_API_KEY` | NVIDIA |
| `OPENROUTER_API_KEY` | OpenRouter |
| `GITHUB_TOKEN` | Required — a GitHub personal access token; startup fails without it (7.2) |

The provider keys are required here for the same reason they are required
on the Render web service: the worker runs the queued jobs, so a job that calls
a model raises `MANTLE_API_KEY is not configured.` without them. Note that the
worker builds a full `ModelRouter` at startup, yet does not fail immediately —
`build_model_router()` resolves from the model catalogue regardless of
credentials, so the error only surfaces when a job first calls a model. A green
worker run therefore does not prove the keys are correct.

`REDIS_URL` must be the **external** URL. The internal URL resolves only over
Render's private network and will not work from a runner. Copy it from the
Key Value instance's Connect menu. The API keeps using the internal URL via
`fromService`, which Render documents as supported: free web services "can't
_receive_ private network traffic" but "can _send_ private network requests to
your data stores ... in the same region".

`GITHUB_APP_PRIVATE_KEY` is written to
`/etc/secrets/github-app-private-key.pem` at runtime to satisfy
`GITHUB_PRIVATE_KEY_PATH`. Paste the whole PEM, delimiters included.

### 7.4 Run migrations

`scripts/bootstrap.py` is idempotent — it skips statements that fail with
"already exists" / "duplicate" — so it is safe to re-run. The `migrate`
workflow runs it automatically on every push to `render-deployment`, and you
can trigger it manually from the Actions tab.

Run it **before** the first API deploy if you are deploying to a fresh Neon
database. It needs only `DATABASE_URL`, which the workflow already reads.

### 7.5 Trigger the worker

Scheduled runs fire every 5 minutes. For a demo, run it on demand from the
Actions tab instead of waiting. The worker sets `RQ_BURST=1`, which makes RQ
return as soon as every queue is empty rather than blocking forever.

GitHub automatically disables scheduled workflows in public repositories after
60 days without repository activity. Re-enable by running the workflow once
manually.

#### The workflows must exist on the default branch

`draftly-agent-backend`'s default branch is **`master`**, not
`render-deployment`. GitHub's documentation states that for the `schedule`
event "Scheduled workflows will only run on the default branch", and for
`workflow_dispatch` that "This event will only trigger a workflow run if the
workflow file exists on the default branch."

**If `.github/workflows/rq-worker.yml` is not present on `master`, the schedule
will never fire and the "Run workflow" button will not appear.** Copy both
workflow files onto `master` to activate them.

Because of that constraint the worker workflow pins its checkout to
`ref: render-deployment`. For a `schedule` run, `GITHUB_SHA` is the last commit
on the default branch, and `master` does not contain `resolve_burst_mode()`;
without the pin it would call `worker.work(burst=False)`, block on `BLPOP`
forever, and be killed by the 330-minute timeout on every run.

The `migrate` workflow is triggered by `push` to `render-deployment` rather
than by a schedule, so it does not depend on which branch holds the file — a
push event is evaluated against the pushed ref.

### 7.6 Verify

1. Actions tab shows a green `rq-worker` run
2. Run log contains `RQ worker work loop starting` with `burst=true`
3. Enqueue a job from the UI; it is picked up within 5 minutes, or immediately
   after a manual dispatch
4. `draftly-api` logs show no Redis or database connection errors
5. `GET /health` returns `200` after a deploy

Note that `GET /health/ready` currently returns `500`. This is a pre-existing
bug unrelated to deployment — `api/routes/health.py` reads
`application.dependencies.memory`, but `ApplicationDependencies` has no `memory`
field, so the handler raises `AttributeError`. Use `/health` for liveness, which
is what `healthCheckPath` is set to.

### 7.7 Known limits

- **Free web services can be suspended for outbound traffic.** Render may
  suspend a free web service that "initiates an uncommonly high volume of
  traffic over the public internet", explicitly including accessing an
  external database and invoking external APIs — which is exactly this
  workload's profile. Restoring it requires moving to a paid plan.
- Queue latency is up to 5 minutes on the scheduled path.
- A job still running when the 330-minute timeout fires is killed and
  recovered on the next boot by `reconcile_stale_on_boot`.
- **No graceful shutdown window.** Free services cannot use
  `maxShutdownDelaySeconds`, so Render kills them on its own schedule rather
  than draining. In-flight HTTP requests and open SSE dashboard streams can be
  dropped without warning on a spin-down or deploy. The paid Blueprint keeps
  `maxShutdownDelaySeconds: 120`; the free path cannot.
- `draftly-kv` is in-memory: a restart drops queued jobs.
- The free web service spins down after 15 minutes idle, which can drop a
  long-lived SSE dashboard stream.
- 0.1 CPU / 512 MB may be tight for the PDF and agent workloads.
