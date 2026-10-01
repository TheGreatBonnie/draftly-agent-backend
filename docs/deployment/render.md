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
