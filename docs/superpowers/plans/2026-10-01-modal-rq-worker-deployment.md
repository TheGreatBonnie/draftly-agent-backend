# Modal RQ Worker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task.

**Goal:** Deploy the existing burst-mode RQ worker to Modal on a one-minute schedule while keeping the API, PostgreSQL, and Redis-compatible queue on their current providers.

**Architecture:** A Modal scheduled function builds the existing Render Dockerfile and runs `python -m workers.rq_worker` with `RQ_BURST=1`. It scales to zero between invocations, permits only one worker container, and connects to Render Key Value through its external TLS URL. Modal is the only hosted RQ worker on this branch.

**Tech Stack:** Python 3.11, Modal SDK 1.6.0, RQ, Redis/Valkey, Docker, GitHub Actions, pytest, `uv`

**Spec:** Approved Modal-versus-Railway deployment analysis from 2026-10-01.

## Global Constraints

- Work in the `draftly-agent-backend` submodule on `modal-deployment`.
- Preserve unrelated uncommitted changes and never read or commit `.env`, `secrets/`, or PEM contents.
- Keep `decode_responses=False` and the existing worker signal handling.
- Use the external TLS `rediss://` URL on Modal, never Render's private Redis URL.
- Use one scheduled Modal container, a one-minute cron, and `RQ_BURST=1`.
- Do not run database migrations from the Modal worker.
- Run `graphify update .` after code changes.

## Task 1: Portable GitHub App authentication

- [x] Make the inline-PEM tests independent of local settings.
- [x] Require `GITHUB_APP_PRIVATE_KEY` in both Render Blueprints and remove stale file paths where no file is mounted.
- [x] Verify the focused authentication and deployment tests.

## Task 2: Modal worker application

- [x] Add Modal 1.6.0 to the development dependencies, with the Intel macOS `cbor2` compatibility pin.
- [x] Add `infra/modal/rq_worker.py` using the existing Render Dockerfile.
- [x] Schedule `drain_rq` every minute with 0.125 CPU, 512 MiB memory, a 21,000-second timeout, one maximum container, and a two-second scale-down window.
- [x] Run the worker subprocess as `draftly:render-secrets` with `RQ_BURST=1`.
- [x] Cover the command, environment, and runtime configuration with deployment tests.

## Task 3: Continuous deployment

- [x] Add `.github/workflows/deploy-modal-worker.yml` for pushes to `modal-deployment`.
- [x] Authenticate with `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` only.
- [x] Deploy with the locked environment and rolling strategy.
- [x] Test the workflow contract.

## Task 4: Documentation and cutover

- [x] Add `docs/deployment/modal.md` with setup, secret, verification, cost, and rollback instructions.
- [x] Update the Render deployment guide and free Blueprint comments.
- [x] Remove the GitHub Actions RQ worker and the Render worker service from this branch.
- [x] Test that Modal is the sole hosted worker.

## Task 5: Verification

- [ ] Run deployment, worker, and authentication tests.
- [ ] Run the full test and lint suites.
- [ ] Build the shared Docker image.
- [ ] Run `graphify update .`.
- [ ] Verify no secret material is staged.

## Acceptance Criteria

- Modal invokes the burst worker every minute and scales to zero between drains.
- At most one Modal worker container runs concurrently.
- Empty queues exit cleanly and queued jobs use the existing three RQ queues.
- GitHub App authentication works without a mounted PEM file.
- No GitHub Actions or Render worker duplicates Modal on `modal-deployment`.
- Projected Modal usage remains below the recurring $30 allowance.
