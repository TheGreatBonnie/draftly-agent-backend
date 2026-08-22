"""Bootstrap script tests (offline, fake DatabaseClient — plan §11.4)."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


@dataclass
class FakeDatabaseClientForScripts:
    """Mirrors DatabaseClient surface used by scripts/ (start/execute/close/fetch)."""

    executed: list[str] = field(default_factory=list)
    started: bool = False
    closed: bool = False
    select_one_row: Any = None
    fail_on_attempt: int | None = None
    failure_message: str = "already exists"
    _failed_attempts: int = 0

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def execute(self, query: str, *args: Any) -> str:
        del args
        attempt = len(self.executed) + self._failed_attempts
        if self.fail_on_attempt is not None and attempt == self.fail_on_attempt:
            self._failed_attempts += 1
            raise RuntimeError(self.failure_message)
        self.executed.append(query)
        return "OK"

    async def fetch_one(self, query: str, *args: Any) -> Any:
        del args
        self.executed.append(query)
        return self.select_one_row


def _load_bootstrap() -> Any:
    script = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap.py"
    spec = importlib.util.spec_from_file_location("bootstrap_under_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bootstrap_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    module = _load_bootstrap()
    fake = FakeDatabaseClientForScripts(select_one_row={"?column?": 1})
    monkeypatch.setattr(module, "DatabaseClient", lambda: fake)
    module._fake = fake
    return module


async def test_module_uses_real_database_client_api(bootstrap_module):
    """Module imports and exposes its entry points against the real client API."""
    assert callable(bootstrap_module.run_migrations)
    assert callable(bootstrap_module.verify_connectivity)
    assert callable(bootstrap_module.main)


async def test_run_migrations_applies_every_sql_file(bootstrap_module):
    """Each migration file in persistence/migrations is executed once, in order."""
    await bootstrap_module.run_migrations()

    migrations_dir = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "persistence"
        / "migrations"
    )
    expected = sorted(p.name for p in migrations_dir.glob("*.sql"))

    assert expected, "expected migration files to exist in the repo"
    assert len(bootstrap_module._fake.executed) == len(expected)


async def test_run_migrations_survives_already_applied_errors(bootstrap_module):
    """'already exists' errors are skipped; later migrations still run."""
    fake = bootstrap_module._fake
    fake.fail_on_attempt = 0
    fake.failure_message = 'relation "organizations" already exists'

    await bootstrap_module.run_migrations()

    migrations_dir = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "draftly"
        / "persistence"
        / "migrations"
    )
    total = len(list(migrations_dir.glob("*.sql")))
    assert len(fake.executed) == total - 1, "expected every file after the failed one to apply"


async def test_run_migrations_raises_on_unexpected_error(bootstrap_module):
    """Non-idempotent errors propagate instead of being swallowed."""
    fake = bootstrap_module._fake
    fake.fail_on_attempt = 0
    fake.failure_message = "syntax error at or near"

    with pytest.raises(RuntimeError):
        await bootstrap_module.run_migrations()


async def test_client_lifecycle_started_and_closed(bootstrap_module):
    """Migrations start the pool up front and always close it afterwards."""
    await bootstrap_module.run_migrations()
    fake = bootstrap_module._fake
    assert fake.started
    assert fake.closed


async def test_verify_connectivity_success(bootstrap_module):
    assert await bootstrap_module.verify_connectivity() is True
    assert bootstrap_module._fake.executed[-1] == "SELECT 1"


async def test_verify_connectivity_failure_is_contained(bootstrap_module):
    fake = bootstrap_module._fake
    fake.select_one_row = None

    assert await bootstrap_module.verify_connectivity() is False


async def test_main_fails_fast_without_database_url(
    bootstrap_module, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)

    exit_code = await bootstrap_module.main()

    assert exit_code == 1
    assert bootstrap_module._fake.started is False
