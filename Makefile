.PHONY: help sync run run-agentcore test test-live lint fmt typecheck migrate docker-build docker-build-agentcore docker-push docker-push-agentcore clean probe-models probe-token-factory

help:
	@echo "Draftly - Available targets:"
	@echo "  sync         Install dependencies with uv"
	@echo "  run          Run the API server (main.py)"
	@echo "  run-agentcore Run the AgentCore runtime server (port 8080)"
	@echo "  worker-rq    Run the unified RQ worker (workflow, indexing, evaluation queues)"
	@echo "  worker-event Run the event worker (API under uvicorn)"
	@echo "  test         Run offline tests"
	@echo "  test-live    Run live integration tests (requires DRAFTLY_LIVE=1)"
	@echo "  lint         Run ruff check"
	@echo "  fmt          Run ruff format"
	@echo "  typecheck    Run mypy"
	@echo "  migrate      Run database bootstrap/migrations"
	@echo "  probe-token-factory  Live-probe Nebius Token Factory (TTFT, tools, embeddings)"
	@echo "  docker-build Build API and worker images"
	@echo "  docker-push  Push images to ECR (requires AWS auth)"
	@echo "  clean        Remove build artifacts"

sync:
	uv sync --frozen

run:
	python main.py

run-agentcore:
	python agentcore_server.py

worker-event:
	python -m workers.event_worker

# The unified RQ worker replaces the former workflow, indexing, and
# evaluation worker entrypoints (see workers/rq_worker.py).
worker-rq:
	python -m workers.rq_worker

test:
	uv run pytest -q

test-live:
	DRAFTLY_LIVE=1 uv run pytest -q -m integration

probe-models:
	uv run python tests/scripts/probe_models.py

probe-token-factory:
	uv run python tests/scripts/probe_token_factory.py

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy src

migrate:
	python scripts/bootstrap.py

docker-build:
	docker build -f docker/Dockerfile.api -t draftly/api:latest .
	docker build -f docker/Dockerfile.worker -t draftly/worker:latest .

docker-push:
	aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com
	docker tag draftly/api:latest $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com/draftly/api:latest
	docker tag draftly/worker:latest $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com/draftly/worker:latest
	docker push $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com/draftly/api:latest
	docker push $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com/draftly/worker:latest

docker-build-agentcore:
	docker buildx create --use 2>/dev/null || true
	docker buildx build --platform linux/arm64 -f docker/Dockerfile.agentcore -t draftly/agentcore:latest --load .

docker-push-agentcore:
	aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com
	docker tag draftly/agentcore:latest $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com/draftly-agentcore:latest
	docker push $$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com/draftly-agentcore:latest

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true