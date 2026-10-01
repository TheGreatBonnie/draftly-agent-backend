# GitHub Actions Burst Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Draftly RQ worker as a short-lived GitHub Actions job so the stack is fully hosted at $0, replacing the paid Render Background Worker.

**Architecture:** The RQ worker gains an `RQ_BURST` env gate that calls `worker.work(burst=True)`, making it drain every queued job and exit instead of blocking forever. A scheduled workflow runs that worker on a 5-minute cadence and on manual dispatch, with an outer `timeout` as a backstop. The API stays on Render's free web service and the queue on Render free Key Value.

**Tech Stack:** Python 3.11, RQ 2.11, `uv`, pytest + fakeredis, GitHub Actions, Render Blueprint

**Spec:** Conversation analysis of 2026-10-01. Load-bearing facts verified against vendor docs:
- GitHub billing docs: "The use of standard GitHub-hosted runners is free: In public repositories" — confirmed backend is public via `gh repo view` (`isPrivate: false`)
- GitHub limits docs: GitHub-hosted job execution limit is 6 hours (we use 350 min)
- GitHub billing docs: "Larger runners are always charged for, even when used by public repositories" — must use `ubuntu-latest`
- GitHub docs: "In a public repository, scheduled workflows are automatically disabled when no repository activity has occurred in 60 days"
- Render Key Value docs: external URL requires an IP allowlist entry; free plan is in-memory only
- Render docs recommend `noeviction` for job queues (already set)

## Global Constraints

- **Hard constraint: no payment card.** Any plan change that triggers a card prompt disqualifies the option.
- Target branch is `render-deployment` in the **submodule** `draftly-agent-backend`, NOT the parent repo. It is 7 commits ahead of `master`.
- **Never use a GitHub "larger runner"** — always billed, even on public repos.
- `uv` is the toolchain (`uv sync --frozen --no-dev`); plain `pip install` is wrong for this repo.
- pytest config lives in `pyproject.toml` under `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` and `pythonpath = ["."]`.
- Worker entrypoint is `python -m workers.rq_worker` (module, not script path).
- The worker must keep `decode_responses=False` on its Redis connection — RQ zlib-compresses job payloads. Do not "clean this up."
- Existing signal handling in `workers/rq_worker.py` (SIGINT/SIGTERM → `worker.request_stop`) must not be removed or altered.

---

### Task 1: Burst-mode worker gate

**Files:**
- Modify: `workers/rq_worker.py` (imports near line 11; `main()` lines 60-131, specifically the `worker.work()` call at line 121)
- Test: `tests/test_workers/test_rq_burst_mode.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `workers.rq_worker.resolve_burst_mode(env: Mapping[str, str] | None = None) -> bool` — returns `True` only when the value is exactly `"1"`. Task 3 depends on this existing and being importable without side effects.

**Context you need:** `workers/rq_worker.py:121` currently calls `worker.work()` with no arguments. Burst mode is what makes the worker exit when the queue empties. The function is separate from `main()` purely so it is testable without booting an application, Redis, or a database.

- [ ] **Step 1: Write the failing test**

Create `tests/test_workers/test_rq_burst_mode.py`:

```python
"""The worker must exit once the queue drains when RQ_BURST=1.

GitHub Actions runs the worker as a short-lived job: it drains whatever is
queued and returns instead of blocking forever on BLPOP. `work(burst=True)`
is the RQ primitive that does this. It is gated behind an env var so local
`python -m workers.rq_worker` keeps its normal long-running behaviour.

`resolve_burst_mode` is imported inside each test rather than at module
scope because importing `workers.rq_worker` pulls in
`draftly.app.lifecycle` and the settings model, which reads DATABASE_URL and
REDIS_URL. Deferring the import keeps this unit test free of that.
"""

from __future__ import annotations

import pytest


def test_burst_disabled_by_default():
    from workers.rq_worker import resolve_burst_mode

    assert resolve_burst_mode({}) is False


@pytest.mark.parametrize(
    "value",
    ["", "0", "true", "True", "yes", "on", "2", " 1"],
)
def test_burst_requires_exact_string_one(value: str):
    """Only the exact string "1" enables burst mode.

    Stricter than a truthy parse on purpose: a typo must not silently
    turn a 6-hour CI job into an indefinite blocking worker, and must not
    silently disable burst either.
    """
    from workers.rq_worker import resolve_burst_mode

    assert resolve_burst_mode({"RQ_BURST": value}) is False


def test_burst_enabled_for_exact_one():
    from workers.rq_worker import resolve_burst_mode

    assert resolve_burst_mode({"RQ_BURST": "1"}) is True


def test_burst_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch):
    from workers.rq_worker import resolve_burst_mode

    monkeypatch.setenv("RQ_BURST", "1")
    assert resolve_burst_mode() is True

    monkeypatch.delenv("RQ_BURST", raising=False)
    assert resolve_burst_mode() is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_workers/test_rq_burst_mode.py -v`
Expected: FAIL — collection error, `ImportError: cannot import name 'resolve_burst_mode'`

- [ ] **Step 3: Implement the resolver**

In `workers/rq_worker.py`, add `os` to the imports (it is not currently imported) so the block reads:

```python
from __future__ import annotations

import os
import signal
from collections.abc import Mapping
from typing import Any

import structlog
from rq import SimpleWorker
```

Then insert this module-level helper immediately before `class InitLockAwareWorker`:

```python
def resolve_burst_mode(env: Mapping[str, str] | None = None) -> bool:
    """Return True when the worker should drain-and-exit instead of blocking.

    GitHub Actions runs the worker as a short-lived job, so it must exit once
    the queues are empty instead of blocking on BLPOP forever. Only the exact
    string "1" enables it, so a typo never silently changes worker lifetime
    in either direction.
    """
    source = os.environ if env is None else env
    return source.get("RQ_BURST") == "1"
```

- [ ] **Step 4: Wire it into `main()`**

Replace the bare call at `workers/rq_worker.py:121`:

```python
    try:
        worker.work()
```

with:

```python
    burst = resolve_burst_mode()
    log.info(
        "RQ worker work loop starting",
        queues=queue_names,
        prefix=settings.rq_queue_prefix,
        burst=burst,
    )

    try:
        worker.work(burst=burst)
```

Keep the existing `except KeyboardInterrupt` / `except Exception` / `finally: conn.close()` blocks exactly as they are.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_workers/test_rq_burst_mode.py -v`
Expected: **11 passed** — 1 (`burst_disabled_by_default`) + 8 (parametrized `burst_requires_exact_string_one`) + 1 (`burst_enabled_for_exact_one`) + 1 (`burst_reads_os_environ_by_default`)

- [ ] **Step 6: Verify no regression in existing worker tests**

Run: `uv run pytest tests/test_workers/ -v`
Expected: all pass. `test_rq_connection_decode.py` is the important one — it locks `decode_responses=False`.

- [ ] **Step 7: Lint**

Run: `uv run ruff check workers/rq_worker.py tests/test_workers/test_rq_burst_mode.py`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add workers/rq_worker.py tests/test_workers/test_rq_burst_mode.py
git commit -m "feat(worker): support burst drain-and-exit via RQ_BURST"
```

---

### Task 2: Free-tier Render Blueprint

**Files:**
- Create: `render.free.yaml` (repo root)

**Interfaces:**
- Consumes: nothing from earlier tasks. Independent of Task 1 — the Blueprint is deploy-time config, and the worker it references is Task 3's workflow.
- Produces: a Blueprint file at `render.free.yaml`, deployable via Render's dashboard "Blueprint" flow with `render.yaml` path overridden.

**Context you need:** This is a **separate file**, not an edit to `render.yaml`. `render.yaml` is the known-good paid deployment and must keep working; the card-happy paid config stays intact for anyone who adds a payment method later. Creating a second file means the two configurations never conflict.

The three changes vs `render.yaml`: plans go to `free`, `ipAllowList` opens to the world, and the `worker` service is deleted entirely.

**CORRECTION (found during execution, verified against Render docs):** `preDeployCommand` must be **removed**, not copied. Render's deploys doc states verbatim: "The pre-deploy command is available for paid web services, private services, and background workers." A free web service silently ignores it, so `scripts/bootstrap.py` would never run and the API would boot against an unmigrated schema. Migrations move to `.github/workflows/migrate.yml` (Task 3), which is $0 for the same reason the worker is.

**Also verified:** Render's free-tier doc states free web services "can't _receive_ private network traffic" but "can _send_ private network requests to your data stores and paid services in the same region." The `fromService` internal `REDIS_URL` therefore works for the API (both services are `oregon`), while the Actions worker must use the external URL via `ipAllowList`.

Deleting the worker service is the whole point of this plan — Render has **no free tier for `type: worker`**. The worker is replaced by `.github/workflows/rq-worker.yml` from Task 3.

GitHub-hosted runners have huge, shifting IP ranges, so allowlisting them properly means several hundred CIDRs. `0.0.0.0/0` is the pragmatic choice for a hackathon: the instance is still password-authenticated, and it holds only queued job IDs.

- [ ] **Step 1: Create the Blueprint**

Create `render.free.yaml` with exactly this content:

```yaml
# Free-tier Blueprint ($0, no payment card).
#
# Differences from render.yaml:
#   - keyvalue + web run on the `free` plan
#   - draftly-kv accepts external connections so the GitHub Actions worker
#     (whose egress IP is not statically assignable) can reach it
#   - the `worker` service is REMOVED: Render has no free tier for
#     `type: worker`. The RQ worker runs as a short-lived job in
#     .github/workflows/rq-worker.yml instead.
#
# Security note: ipAllowList 0.0.0.0/0 exposes the queue to the internet,
# protected only by the instance password. It holds queued job IDs only.
# Revert to [] after the demo if the worker moves to a static egress IP.

services:
  - type: keyvalue
    name: draftly-kv
    region: oregon
    plan: free
    maxmemoryPolicy: noeviction
    ipAllowList:
      - source: 0.0.0.0/0
        description: github-actions-runners

  - type: web
    name: draftly-api
    runtime: docker
    region: oregon
    plan: free
    branch: render-deployment
    autoDeployTrigger: commit
    dockerfilePath: ./docker/Dockerfile.render
    dockerContext: .
    dockerCommand: /bin/sh -c 'exec uvicorn draftly.app.api.app:app --host 0.0.0.0 --port "${PORT:-10000}"'
    # NO preDeployCommand: Render gates it to paid services and free silently
    # ignores it. Migrations run from .github/workflows/migrate.yml instead.
    healthCheckPath: /health
    maxShutdownDelaySeconds: 120
    envVars:
      - key: ENVIRONMENT
        value: production
      - key: LOG_LEVEL
        value: INFO
      - key: RQ_ENABLED
        value: "true"
      - key: STRANDS_SESSION_STORAGE
        value: database
      - key: DATABASE_URL
        sync: false
      - key: REDIS_URL
        fromService:
          type: keyvalue
          name: draftly-kv
          property: connectionString
      - key: DRAFTLY_ENABLED_PROVIDERS
        sync: false
      - key: FRONTEND_URL
        sync: false
      - key: ALLOWED_ORIGINS
        sync: false
      - key: PUBLIC_API_URL
        sync: false
      - key: APP_URL
        sync: false
      - key: REVIEW_DASHBOARD_URL
        sync: false
      - key: CLERK_PUBLISHABLE_KEY
        sync: false
      - key: CLERK_SECRET_KEY
        sync: false
      - key: CLERK_SIGNING_SECRET
        sync: false
      - key: GITHUB_APP_ID
        sync: false
      - key: GITHUB_APP_SLUG
        sync: false
      - key: GITHUB_CLIENT_ID
        sync: false
      - key: GITHUB_CLIENT_SECRET
        sync: false
      - key: GITHUB_WEBHOOK_SECRET
        sync: false
      - key: GITHUB_PRIVATE_KEY_PATH
        value: /etc/secrets/github-app-private-key.pem
```

Note what was **dropped** relative to `render.yaml` and why:

- `persistenceMode: journal-snapshot` — free Key Value is in-memory only; Render ignores or rejects this on the free plan.
- `preDeployCommand` — paid-only feature, silently ignored on free (see correction above).
- `plan: 256mb`, `plan: 1c-2g` → `free`
- The entire `- type: worker` block
- `GITHUB_CLIENT_ID`/`SECRET` are retained here (API-only secrets still live on the web service)

- [ ] **Step 2: Validate against Render's JSON schema**

Run: `curl -s https://render.com/schema/render.yaml.json -o /tmp/render-schema.json && uv run python -c "
import json, sys
schema = json.load(open('/tmp/render-schema.json'))
print('schema fetched OK, keys:', sorted(schema.keys())[:6])
"`

Then validate structurally:

```bash
uv run python - <<'PY'
import sys
try:
    import yaml
except ImportError:
    sys.exit("pyyaml missing; run: uv run --with pyyaml python -")
doc = yaml.safe_load(open("render.free.yaml"))
svcs = doc["services"]
types = [s["type"] for s in svcs]
assert "worker" not in types, f"worker service must be absent, got {types}"
assert types.count("web") == 1, types
kv = next(s for s in svcs if s["type"] == "keyvalue")
assert kv["plan"] == "free", kv["plan"]
assert kv["ipAllowList"][0]["source"] == "0.0.0.0/0", kv["ipAllowList"]
assert kv["maxmemoryPolicy"] == "noeviction", kv["maxmemoryPolicy"]
web = next(s for s in svcs if s["type"] == "web")
assert web["plan"] == "free", web["plan"]
assert web["branch"] == "render-deployment", web["branch"]
assert web["dockerfilePath"] == "./docker/Dockerfile.render"
print("PASS: free blueprint structure verified")
PY
```
Expected: `PASS: free blueprint structure verified`

- [ ] **Step 3: Confirm the paid Blueprint is untouched**

Run: `git diff --stat render.yaml`
Expected: empty output — `render.yaml` must be unmodified.

- [ ] **Step 4: Commit**

```bash
git add render.free.yaml
git commit -m "deploy(render): add free-tier Blueprint without a worker service"
```

---

### Task 3: Scheduled worker workflow

**Files:**
- Create: `.github/workflows/rq-worker.yml`

**Interfaces:**
- Consumes: `RQ_BURST` gate from Task 1 (`resolve_burst_mode`), the `python -m workers.rq_worker` entrypoint, and `render.free.yaml` from Task 2.
- Produces: secrets `DATABASE_URL`, `REDIS_URL`, `GITHUB_APP_PRIVATE_KEY` that Task 4 documents.

**Context you need:** `GITHUB_PRIVATE_KEY_PATH` points at `/etc/secrets/github-app-private-key.pem` — a **file on disk**, not an env var. Render mounts it from a secret file; GitHub Actions has no such mount, so the workflow must write the PEM to that exact path before starting the worker. `render.yaml`/`.dockerignore` exclude `secrets/` from the image, so this file must be created at runtime, never committed.

Two timeouts, deliberately different jobs:
- `timeout 330` (the `timeout` command) bounds the worker process so it exits before the 350-minute job cap
- `timeout-minutes: 350` is GitHub's own hard kill at 6 hours

The gap between 330 and 350 gives RQ's SIGTERM warm shutdown room to run.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/rq-worker.yml`:

```yaml
# Runs the Draftly RQ worker as a short-lived job so no always-on worker
# host is required. Render has no free tier for `type: worker`, and a
# continuously-polling RQ worker costs real money on every per-second
# billing platform. Draining the queue and exiting costs minutes, and
# standard GitHub-hosted runner minutes are free in public repositories.
#
# Triggers:
#   schedule       - every 5 min (GitHub's minimum schedule interval)
#   workflow_dispatch - manual, for demos where 5 min is too slow to wait
#
# Runner: MUST stay `ubuntu-latest`. Larger runners are billed even for
# public repositories.

name: rq-worker

on:
  schedule:
    - cron: "*/5 * * * *"
  workflow_dispatch:

# GitHub disables scheduled workflows in public repos after 60 days of
# repository inactivity. Manually run once from the Actions tab to re-enable.
concurrency:
  group: rq-worker
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  worker:
    runs-on: ubuntu-latest
    # GitHub-hosted jobs hard-stop at 6h. 350 min leaves headroom for the
    # 330-minute internal timeout plus warm shutdown.
    timeout-minutes: 350
    env:
      RQ_BURST: "1"
      ENVIRONMENT: production
      LOG_LEVEL: INFO
      RQ_ENABLED: "true"
      STRANDS_SESSION_STORAGE: database
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      REDIS_URL: ${{ secrets.REDIS_URL }}
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          version: "0.12.10"

      - name: Install dependencies
        run: uv sync --frozen --no-dev

      # The worker expects a PEM file on disk at this path (it reads the
      # same value Render mounts as a secret file). It must be written at
      # runtime: .dockerignore and the public repo both rule out committing it.
      - name: Materialize GitHub App private key
        run: |
          mkdir -p /etc/secrets
          printf '%s\n' "$GITHUB_APP_PRIVATE_KEY" > /etc/secrets/github-app-private-key.pem
          chmod 600 /etc/secrets/github-app-private-key.pem

      # burst=True (set via RQ_BURST) makes RQ return as soon as all queues
      # are empty -- no SIGTERM needed. `timeout 330` is only a backstop
      # for a job that hangs. Do NOT add `--max-idle-time`: that is an `rq`
      # CLI flag, and workers/rq_worker.py does not parse argv.
      - name: Drain the queues
        run: timeout 330 python -m workers.rq_worker
        env:
          GITHUB_PRIVATE_KEY_PATH: /etc/secrets/github-app-private-key.pem
          GITHUB_APP_ID: ${{ secrets.GITHUB_APP_ID }}
          GITHUB_APP_SLUG: ${{ secrets.GITHUB_APP_SLUG }}

      - name: Upload logs on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: rq-worker-logs
          path: /tmp/*.log
          retention-days: 3
          if-no-files-found: ignore
```

Notes on choices that look odd:
- `cancel-in-progress: false` — a cancelled worker drops the job it was running. Queue drains must finish.
- `contents: read` — least privilege; the worker only reads the repo.
- The artifact upload is `if-no-files-found: ignore` because the worker does not write `/tmp/*.log` by default; it avoids a red X on routine failures.
- `timeout` returns exit code 124. That is expected on a full-window drain and is not a failure to page on.

- [ ] **Step 2: Validate YAML syntax**

Run: `uv run --with pyyaml python -c "
import yaml
d = yaml.safe_load(open('.github/workflows/rq-worker.yml'))
assert d[True]['schedule'][0]['cron'] == '*/5 * * * *', d[True]
assert 'workflow_dispatch' in d[True]
job = d['jobs']['worker']
assert job['runs-on'] == 'ubuntu-latest', job['runs-on']
assert job['timeout-minutes'] == 350
assert job['env']['RQ_BURST'] == '1'
print('PASS: workflow structure verified')
"
```
Expected: `PASS: workflow structure verified`

- [ ] **Step 3: Confirm no secret was committed**

Run: `grep -rniE 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' .github/ render.free.yaml && echo "FAIL: private key committed" || echo "PASS: no private key in repo"`
Expected: `PASS: no private key in repo`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/rq-worker.yml
git commit -m "ci: run RQ worker as scheduled burst job on GitHub Actions"
```

---

### Task 4: Deployment documentation

**Files:**
- Modify: `docs/deployment/render.md` (add a section after the existing "## 6. Verify production" section)

**Interfaces:**
- Consumes: `render.free.yaml` (Task 2), `.github/workflows/rq-worker.yml` (Task 3), `RQ_BURST` (Task 1).
- Produces: nothing consumed by later tasks. This is the final task.

**Context you need:** `docs/deployment/render.md` currently documents the **paid** Blueprint end to end and says "The Blueprint creates: a paid draftly-api... a paid draftly-worker... a paid, internal-only draftly-kv." Do not rewrite that — it stays correct for the paid path. Append a clearly-marked free-tier section so both paths are documented.

The GitHub Actions secrets must be listed explicitly; that is the single most common setup failure, since a missing secret produces a generic connection error rather than a clear message.

- [ ] **Step 1: Append the free-tier section**

Append to `docs/deployment/render.md`:

```markdown

## 7. Free-tier deployment (no payment card)

Render's Background Worker has **no free tier**, so the paid Blueprint
cannot be used without a card. The free path keeps the API and queue on
Render's free tier and moves the RQ worker to GitHub Actions.

| Component | Where | Cost |
| --- | --- | --- |
| FastAPI | Render free Web Service (`draftly-api`) | $0 |
| Queue and event bus | Render free Key Value (`draftly-kv`, Valkey 8) | $0 |
| RQ worker | GitHub Actions, burst job | $0 (public repo) |
| PostgreSQL | Neon | $0 |

Why GitHub Actions: standard GitHub-hosted runner minutes are free in
public repositories, and a burst worker costs minutes rather than
continuously billing per second. Render, Railway, and Modal all bill a
polling worker by the second or offer no free worker tier.

### 7.1 Deploy the API and queue

Create a Blueprint from `render.free.yaml` instead of `render.yaml`
(New → Blueprint → select branch `render-deployment` → set the Blueprint
path to `render.free.yaml`). Set the same `sync: false` secrets as
sections 3 and 4.

Two differences from the paid Blueprint: `draftly-kv` opens its external
connection to `0.0.0.0/0` so the Actions runner can reach it (GitHub's
egress ranges are too large and too shifting to allowlist individually),
and there is no `draftly-worker` service.

Free Key Value is **25 MB and in-memory only**. Queued jobs are lost if
the instance restarts. Render may also suspend a free web service for
high service-initiated outbound traffic, which this workload does a lot
of (LLM calls, Neon).

### 7.2 Configure the GitHub Actions secrets

Add these under Settings → Secrets and variables → Actions:

| Secret | Source |
| --- | --- |
| `DATABASE_URL` | Neon connection string (same value as on Render) |
| `REDIS_URL` | Render `draftly-kv` **external** connection string (`rediss://...`) |
| `GITHUB_APP_ID` | GitHub App settings |
| `GITHUB_APP_SLUG` | GitHub App settings |
| `GITHUB_APP_PRIVATE_KEY` | Full PEM including `BEGIN`/`END` lines |

`REDIS_URL` must be the **external** URL. The internal URL only resolves
inside Render's network and will not work from a runner. Copy it from the
Key Value instance's Connect menu.

`GITHUB_APP_PRIVATE_KEY` is written to
`/etc/secrets/github-app-private-key.pem` at runtime to satisfy
`GITHUB_PRIVATE_KEY_PATH`. Paste the whole PEM, delimiters included.

### 7.3 Trigger the worker

Scheduled runs fire every 5 minutes. For a demo, run it on demand from
the Actions tab instead of waiting.

GitHub automatically disables scheduled workflows in public repositories
after 60 days without repository activity. Re-enable by running the
workflow once manually.

### 7.4 Verify

1. Actions tab shows a green `rq-worker` run
2. Run log contains `RQ worker work loop starting` with `burst=true`
3. Enqueue a job from the UI; it is picked up within 5 minutes, or
   immediately after a manual dispatch
4. `render logs` on `draftly-api` shows no Redis connection errors

### 7.5 Known limits

- Queue latency is up to 5 minutes on the scheduled path
- A job still running when the 330-minute timeout fires is killed and
  recovered on the next boot by `reconcile_stale_on_boot`
- `draftly-kv` is in-memory: a restart drops queued jobs
- The free web service spins down after 15 minutes idle, which can drop a
  long-lived SSE dashboard stream
```

- [ ] **Step 2: Verify the doc renders and links resolve**

Run: `grep -c 'render.free.yaml' docs/deployment/render.md`
Expected: at least 2 (one in the table-of-intro prose, one in 7.1)

Run: `test -f render.free.yaml && test -f .github/workflows/rq-worker.yml && echo "PASS: referenced files exist"`
Expected: `PASS: referenced files exist`

- [ ] **Step 3: Confirm the paid path is still documented**

Run: `grep -c 'draftly-worker' docs/deployment/render.md`
Expected: at least 1 — the paid Blueprint section must remain intact.

- [ ] **Step 4: Commit**

```bash
git add docs/deployment/render.md
git commit -m "docs(deploy): document the free-tier Render + GitHub Actions path"
```

---

## Post-implementation verification

Run all four, then report actual output:

```bash
cd /Applications/Projects/hackathon/draftly-docs-engineer/draftly-agent-backend
git checkout render-deployment
uv run pytest tests/test_workers/ -v
uv run ruff check workers/ tests/test_workers/
git log --oneline -4
```

Then confirm nothing leaked into the wrong repo:

```bash
cd /Applications/Projects/hackathon/draftly-docs-engineer
git status --porcelain
```

`draftly-agent-backend` will show as modified (the submodule pointer moved). **Do not commit the parent repo** unless the user asks — the parent has unrelated in-progress work in `docs/reference/`.

### Manual steps the user must do (cannot be automated)

1. Create the Render Blueprint from `render.free.yaml`
2. Add the five GitHub Actions secrets
3. Verify the KV external URL is used for `REDIS_URL`
4. Run the workflow manually once to confirm

### Not doing, and why

- **Not replacing `render.yaml`.** The paid Blueprint stays as the
  known-good path for anyone who adds a card later.
- **Not adding `repository_dispatch` triggering.** Would cut latency from
  5 minutes to seconds, but needs a token with dispatch scope stored as a
  Render secret — more secret surface for a demo. Worth adding after the
  scheduled path is proven.
- **Not touching the worker registry or config.** `rq_queue_prefix`,
  `rq_worker_queues`, and `build_rq_queues` are unchanged; burst mode
  consumes the same queues.
- **Not running migrations inside `rq-worker.yml`.** Migrations get their own
  `.github/workflows/migrate.yml` (Task 3) rather than being bolted onto the
  worker's 5-minute schedule. `scripts/bootstrap.py` is idempotent — it skips
  "already exists" — so running it repeatedly is safe, but keeping it separate
  means a failed migration does not fail a queue drain.