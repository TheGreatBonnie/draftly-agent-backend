# Deploy the Draftly API and queue on Render

On `modal-deployment`, Render hosts the FastAPI service and Redis-compatible
queue. The RQ worker runs only on Modal; neither Render nor GitHub Actions
deploys another worker from this branch.

## Architecture

| Component | Service |
|---|---|
| FastAPI | Render Web Service (`draftly-api`) |
| Queue and event bus | Render Key Value (`draftly-kv`) |
| RQ worker | Modal scheduled burst function |
| PostgreSQL | Existing Neon database |
| Next.js UI | Vercel |

Both `render.yaml` and `render.free.yaml` track `modal-deployment`. The regular
Blueprint uses paid Render plans and runs migrations as a pre-deploy command.
The free Blueprint uses free plans and relies on the migration workflow because
free web services do not support the pre-deploy command.

## 1. Validate the image

Build the same application image used by Render and Modal:

```bash
docker buildx build \
  --platform linux/amd64 \
  -f docker/Dockerfile.render \
  -t draftly/backend:modal \
  --load .
```

The image runs as a non-root user, includes the RQ worker and migrations, and
excludes local `.env` and `secrets/` files.

## 2. Create the Blueprint

In Render, choose **New → Blueprint**, connect the backend repository, select
`modal-deployment`, and choose one file:

- `render.yaml` for paid, persistent Render services; or
- `render.free.yaml` for the demo/free deployment.

Neither Blueprint contains a `type: worker` service. Deploy the worker by
following [the Modal worker guide](modal.md).

## 3. Configure the API

The Blueprint supplies fixed runtime values and connects `REDIS_URL` to
`draftly-kv`. Enter each `sync: false` value when Render prompts for it.

Core values:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon pooled PostgreSQL URL |
| `DRAFTLY_ENABLED_PROVIDERS` | Comma-separated enabled providers |
| `FRONTEND_URL` | Production Vercel origin |
| `ALLOWED_ORIGINS` | Allowed browser origins |
| `PUBLIC_API_URL` | Public Render API origin |
| `APP_URL` | Public Render API origin |
| `REVIEW_DASHBOARD_URL` | Production review URL |

Authentication and GitHub values:

| Variable | Source |
|---|---|
| `CLERK_PUBLISHABLE_KEY` | Clerk dashboard |
| `CLERK_SECRET_KEY` | Clerk dashboard |
| `CLERK_SIGNING_SECRET` | Clerk dashboard |
| `GITHUB_TOKEN` | GitHub token |
| `GITHUB_APP_ID` | GitHub App settings |
| `GITHUB_APP_SLUG` | GitHub App settings |
| `GITHUB_CLIENT_ID` | GitHub App settings |
| `GITHUB_CLIENT_SECRET` | GitHub App settings |
| `GITHUB_WEBHOOK_SECRET` | GitHub App settings |
| `GITHUB_APP_PRIVATE_KEY` | Complete PEM text, including delimiters |

Do not set `GITHUB_PRIVATE_KEY_PATH`. The container does not mount a secret
file; it reads the inline `GITHUB_APP_PRIVATE_KEY` value and accepts escaped
`\n` line breaks.

Configure the provider keys required by `DRAFTLY_ENABLED_PROVIDERS`:

- `MANTLE_API_KEY`
- `ORCAROUTER_API_KEY`
- `NVIDIA_API_KEY`
- `OPENROUTER_API_KEY`

The API builds its model router at startup, so these keys are required on the
web service even though Modal executes the queued jobs. Add optional Slack,
Discord, Tavily, and SendGrid credentials only when those integrations are
enabled.

## 4. Configure worker connectivity

The Modal worker cannot use Render's private `REDIS_URL`. Copy the external TLS
connection string from the Render Key Value service into the Modal Secret as
`REDIS_URL`; it should use `rediss://`.

The free Blueprint permits external connections with `0.0.0.0/0` because Modal
does not have a fixed egress address in this setup. The queue is protected by
its password, but exposing it broadly is a security tradeoff. Use a paid
networking option with restricted egress for a hardened production deployment.

Free Render Key Value is small, single-instance, and in-memory. Queued jobs can
be lost when it restarts. Use the paid persistent Blueprint for durable queue
behavior.

## 5. Run migrations

The paid Blueprint runs `python scripts/bootstrap.py` before an API deployment.
For the free Blueprint, run `.github/workflows/migrate.yml`; it executes on
pushes to `modal-deployment` and also supports manual dispatch. Configure its
documented database secrets in GitHub Actions.

## 6. Configure Vercel

Set the UI production environment:

```env
API_URL=https://draftly-api.onrender.com
NEXT_PUBLIC_API_URL=https://draftly-api.onrender.com
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<publishable-key>
CLERK_SECRET_KEY=<secret-key>
```

Changing `NEXT_PUBLIC_API_URL` requires a Vercel redeploy because it is public
build-time configuration.

## 7. Verify production

1. Confirm migrations finish successfully.
2. Confirm `GET /health` returns `200` and `/health/ready` reports healthy
   dependencies.
3. Confirm the Modal worker uses the same database and the Render external TLS
   queue URL.
4. Enqueue a workflow and confirm Modal consumes it on the next one-minute
   burst run.
5. Exercise a GitHub App operation to verify the inline private key.
6. Confirm dashboard SSE requests reach the Render API with
   `Content-Type: text/event-stream`.

Roll back the API from Render's deployment history. Roll back the worker by
deploying the previous known-good commit to Modal; there is no secondary worker
workflow on `modal-deployment`.
