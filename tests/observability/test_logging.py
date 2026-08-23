import json
import logging

import pytest

from draftly.app.config import Settings
from draftly.observability.logging import configure_logging


@pytest.fixture(autouse=True)
def _clean_root_handlers():
    root = logging.getLogger()
    saved = root.handlers[:]
    saved_level = root.level
    root.handlers.clear()
    yield
    root.handlers[:] = saved
    root.setLevel(saved_level)


def _settings(env: str) -> Settings:
    return Settings(environment=env, log_level="INFO")


def test_prod_renders_json_lines_with_expected_keys(capsys):
    configure_logging(_settings("production"))

    import structlog

    structlog.get_logger("test.module").info("hello", org_id="org_1")

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    parsed = json.loads(out[0])
    assert parsed["event"] == "hello"
    assert parsed["level"] == "info"
    assert parsed["logger"] == "test.module"
    assert "timestamp" in parsed
    assert parsed["org_id"] == "org_1"


def test_dev_renders_console_not_json(capsys):
    configure_logging(_settings("development"))

    import structlog

    structlog.get_logger("test.module").info("hello", key="value")

    out = capsys.readouterr().out
    assert "hello" in out
    assert '"event"' not in out  # not JSON


def test_configure_is_idempotent():
    configure_logging(_settings("production"))
    first_count = len(logging.getLogger().handlers)
    configure_logging(_settings("production"))
    assert len(logging.getLogger().handlers) == first_count


def test_foreign_stdlib_entry_gets_same_formatting(capsys):
    configure_logging(_settings("production"))

    logging.getLogger("third.party").warning("foreign hello")

    parsed = json.loads(capsys.readouterr().out.strip())
    assert parsed["event"] == "foreign hello"
    assert parsed["level"] == "warning"
    assert parsed["logger"] == "third.party"
