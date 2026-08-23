"""Tests for RequestLoggingMiddleware: correlation binding + JSON access log."""

import json
import logging
import sys
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from draftly.app.api.middleware.logging import RequestLoggingMiddleware
from draftly.app.config import Settings
from draftly.observability.logging import configure_logging
from draftly.observability.tracing import current_correlation_id

_MARK = "_draftly_handler"


@pytest.fixture(autouse=True)
def _clean_root_handlers() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    httpx_logger = logging.getLogger("httpx")
    saved_httpx_level = httpx_logger.level
    root.handlers.clear()
    # TestClient logs an "HTTP Request: ..." line after the middleware's
    # access line; silence it so the last JSON line is deterministic.
    httpx_logger.setLevel(logging.WARNING)
    configure_logging(Settings(environment="production", log_level="INFO"))
    yield
    root.handlers[:] = saved_handlers
    httpx_logger.setLevel(saved_httpx_level)


def _point_marked_handlers_at_live_stdout() -> None:
    # pytest suspends/closes the setup-phase capsys buffer between phases,
    # so a StreamHandler bound during fixture setup would write to a closed
    # file. Re-point marked handlers at the live call-phase capture instead.
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and getattr(handler, _MARK, False):
            handler.stream = sys.stdout


@pytest.fixture
def client() -> TestClient:
    application = FastAPI()
    application.add_middleware(RequestLoggingMiddleware)

    @application.get("/ping")
    def ping() -> dict[str, str]:
        return {"correlation": current_correlation_id()}

    return TestClient(application, raise_server_exceptions=False)


def test_request_binds_correlation_and_sets_header(client: TestClient) -> None:
    response = client.get("/ping")

    assert response.headers["X-Request-ID"] == response.json()["correlation"]
    assert response.headers["X-Request-ID"] != ""


def test_honors_inbound_x_request_id(client: TestClient) -> None:
    response = client.get("/ping", headers={"X-Request-ID": "abc-123"})

    assert response.json()["correlation"] == "abc-123"


def test_access_line_logged_as_json(
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _point_marked_handlers_at_live_stdout()
    client.get("/ping")

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]

    assert lines, "expected a JSON log line on captured stdout"

    event = json.loads(lines[-1])

    assert event["event"] == "request_completed"
    for key in ("request_id", "method", "path", "status_code", "duration_seconds"):
        assert key in event
