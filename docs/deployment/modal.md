# Deploy the RQ worker on Modal

The `modal-deployment` branch runs the RQ worker as a scheduled Modal Function.
The API and Redis-compatible queue remain on Render. Modal starts one small
container every minute, drains all configured RQ queues in burst mode, and
exits as soon as the queues are empty.

This is the only hosted worker on this branch. The legacy GitHub Actions worker
workflow is intentionally absent.

## Runtime design

The deployment wrapper is `infra/modal/rq_worker.py`. It builds
`docker/Dockerfile.render`, so Modal and Render use the same application image.
The worker runs with:

- cron schedule `* * * * *`;
- `RQ_BURST=1`, which exits after the queues are drained;
- at most one container, preventing overlapping queue drains;
- 0.125 CPU and 512 MiB memory;
- a 21,000-second timeout for a long-running queued job; and
- a two-second scale-down window after the process exits.

The API must give the worker an externally reachable TLS Redis URL. A Render
private-network URL is not reachable from Modal.

## 1. Install and authenticate

From the backend repository:

```bash
uv sync --frozen --extra dev
uv run --extra dev modal setup
```

`modal setup` opens the browser and stores credentials for local deployment.
Do not commit the resulting credentials.

## 2. Create the Modal Secret

Create a Modal Secret named `draftly-worker` in the `main` environment. Add
these application environment variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon pooled PostgreSQL URL |
| `REDIS_URL` | Render Key Value external TLS URL (`rediss://...`) |
| `DRAFTLY_ENABLED_PROVIDERS` | Comma-separated enabled model providers |
| `GITHUB_TOKEN` | GitHub token used while constructing the integration client |
| `GITHUB_APP_ID` | GitHub App ID |
| `GITHUB_APP_SLUG` | GitHub App slug |
| `GITHUB_APP_PRIVATE_KEY` | Complete PEM text, including delimiters |
| `MANTLE_API_KEY` | Mantle provider key |
| `ORCAROUTER_API_KEY` | OrcaRouter provider key |
| `NVIDIA_API_KEY` | NVIDIA provider key |
| `OPENROUTER_API_KEY` | OpenRouter provider key |

Do not set `GITHUB_PRIVATE_KEY_PATH`. Modal receives the private key as inline
PEM text; the application also accepts escaped `\n` line breaks.

Create or update the secret in the Modal dashboard to avoid putting secret
values in shell history. Keep the secret in the same Modal environment used by
the deployment workflow (`main`).

## 3. Deploy and smoke-test

Deploy the scheduled function:

```bash
uv run --extra dev modal deploy infra/modal/rq_worker.py
```

Run an immediate drain without waiting for the next minute:

```bash
uv run --extra dev modal run infra/modal/rq_worker.py
```

In Modal logs, verify that the worker connects to Redis, registers the
`scheduled`, `webhooks`, and `default` queues, drains available jobs, and exits
normally. Then enqueue one application workflow and confirm its RQ job reaches
`finished` status.

## 4. Configure GitHub deployment

The `deploy-modal-worker` workflow deploys pushes to `modal-deployment` when
the worker, application source, image, lockfile, or workflow changes. Add only
these repository Actions secrets:

| Secret | Source |
|---|---|
| `MODAL_TOKEN_ID` | Modal deployment token ID |
| `MODAL_TOKEN_SECRET` | Modal deployment token secret |

Application credentials belong in the `draftly-worker` Modal Secret, not in
GitHub Actions. The workflow uses a rolling deployment strategy and a
concurrency group so a newer deployment supersedes an older pending one.

## Operations and recovery

Inspect function runs and logs in the Modal dashboard. Review current usage
with:

```bash
uv run --extra dev modal billing report --for today --show-resources
```

If a deployment is unhealthy, redeploy the previous known-good commit from
`modal-deployment`. For an immediate retry, run the local entrypoint shown
above. There is no GitHub Actions worker fallback on this branch.
