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


def test_rq_logger_is_filtered_to_warning():
    configure_logging(_settings("production"))

    assert logging.getLogger("rq").level == logging.WARNING


def test_rq_info_is_suppressed_but_errors_survive(caplog):
    """The filter must drop RQ's per-tick chatter without hiding failures.

    ``caplog.set_level`` (no logger argument) lowers the *root* logger and the
    capturing handler only. It must not name "rq", because that would overwrite
    the very filter under test and make this pass vacuously.
    """
    configure_logging(_settings("production"))

    rq_logger = logging.getLogger("rq")
    caplog.set_level(logging.DEBUG)

    rq_logger.info("*** Listening on draftly:webhooks...")
    rq_logger.error("Job failed")

    levels = [r.levelno for r in caplog.records]
    assert logging.INFO not in levels
    assert logging.ERROR in levels


def test_rq_job_description_dumps_are_not_emitted(capsys):
    """RQ renders ``job.description`` -- kwargs included -- twice per job.

    ``Job.description`` is ``get_call_string(func_name, args, kwargs,
    max_length=75)`` (rq/job.py:1462), logged on both dequeue
    (rq/worker/base.py:1136) and success (base.py:1512) at INFO. For
    pull_request and issue_comment events that truncated string carries PR
    titles, branch names and comment bodies.
    """
    configure_logging(_settings("production"))

    logging.getLogger("rq").info(
        "Worker %s: %s: %s (%s)",
        "modal-worker",
        "draftly:webhooks",
        "github_pr.enqueue(event={'pull_request': {'title': 'SECRET TITLE'}})",
        "abc123",
    )

    assert "SECRET TITLE" not in capsys.readouterr().out
