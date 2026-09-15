# Contributing to Draftly

## Development Setup

1. Clone the repo
2. `cd draftly-agent-backend && uv sync`
3. `cp .env.example .env` and fill in required vars
4. `make migrate` to set up the database
5. `make test` to run the test suite

## Code Quality

- `make lint` — ruff check
- `make fmt` — ruff format
- `make typecheck` — mypy

All three must pass before submitting a PR.

## Testing

- `make test` — offline tests (no external services needed)
- `make test-live` — integration tests (requires `DRAFTLY_LIVE=1` and live NeonDB)
- Tests use `StubModel` and `FakeDatabase` by default for fast, isolated runs

## Pull Requests

1. Create a feature branch from `main`
2. Make your changes with tests
3. Run `make lint fmt typecheck test`
4. Submit a PR with a clear description of the change
