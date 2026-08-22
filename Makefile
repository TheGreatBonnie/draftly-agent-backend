.PHONY: help sync run test test-live lint fmt typecheck migrate docker-build docker-push clean

help:
	@echo "Draftly - Available targets:"
	@echo "  sync         Install dependencies with uv"
	@echo "  run          Run the API server (main.py)"
	@echo "  worker-%     Run a worker (event, workflow, indexing, evaluation)"
	@echo "  test         Run offline tests"
	@echo "  test-live    Run live integration tests (requires DRAFTLY_LIVE=1)"
	@echo "  lint         Run ruff check"
	@echo "  fmt          Run ruff format"
	@echo "  typecheck    Run mypy"
	@echo "  migrate      Run database bootstrap/migrations"
	@echo "  docker-build Build API and worker images"
	@echo "  docker-push  Push images to ECR (requires AWS auth)"
	@echo "  clean        Remove build artifacts"

sync:
	uv sync --frozen

run:
	python main.py

worker-event:
	python -m workers.event_worker

worker-workflow:
	python -m workers.workflow_worker

worker-indexing:
	python -m workers.indexing_worker

worker-evaluation:
	python -m workers.evaluation_worker

test:
	uv run pytest -q

test-live:
	DRAFTLY_LIVE=1 uv run pytest -q -m integration

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

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true