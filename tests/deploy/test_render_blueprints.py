"""Render Blueprints must not wrap dockerCommand in a nested shell.

Regression: Render treats a Blueprint's ``dockerCommand`` as a single argv
token rather than a string to word-split, so an embedded ``/bin/sh -c '...'``
is never interpreted by a shell. The container receives the whole thing --
inner quotes included -- as one "command name", and fails at exec time with
exit 127::

    /bin/sh: 1: exec uvicorn draftly.app.api.app:app --port "${PORT:-10000}": not found

The shell reports the *entire* string as the name it could not find, which is
only possible when the string arrived unsplit. The uvicorn binary itself is
fine: it is a runtime dependency and sits on PATH via the image's
``ENV PATH="/app/.venv/bin:${PATH}"``.

These tests parse the real YAML and assert the deployment-time semantics, so a
reintroduced wrapper fails here instead of on Render.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINTS = ["render.yaml", "render.free.yaml"]


def _docker_commands(*, types: tuple[str, ...]) -> list[tuple[str, str]]:
    """Yield (blueprint_name, dockerCommand) for services of the given types.

    Scoped by service type because only a `web` service runs uvicorn and binds
    a port. A `worker` legitimately runs `python -m workers.rq_worker`, which
    has no executable entry point and no socket to bind.
    """
    out: list[tuple[str, str]] = []
    for name in BLUEPRINTS:
        path = REPO_ROOT / name
        if not path.exists():
            continue
        doc = yaml.safe_load(path.read_text())
        for service in doc.get("services", []):
            cmd = service.get("dockerCommand")
            if cmd and service.get("type") in types:
                out.append((name, cmd))
    return out


WEB_COMMANDS = _docker_commands(types=("web",))
WORKER_COMMANDS = _docker_commands(types=("worker",))


@pytest.mark.parametrize("blueprint,command", WEB_COMMANDS)
def test_docker_command_is_not_double_quoted(blueprint: str, command: str):
    """No `/bin/sh -c '...'` wrapper: Render will not word-split it."""
    assert not command.startswith("/bin/sh -c"), (
        f"{blueprint}: dockerCommand starts with a /bin/sh -c wrapper. Render "
        f"passes dockerCommand as one argv token, so the shell never parses the "
        f"nested quoting and exec fails with status 127. Found: {command!r}"
    )


@pytest.mark.parametrize("blueprint,command", WEB_COMMANDS)
def test_docker_command_starts_with_the_executable(blueprint: str, command: str):
    """The first token must be the executable Render will exec."""
    argv = shlex.split(command)
    assert argv[0] == "uvicorn", (
        f"{blueprint}: dockerCommand must exec uvicorn directly so Render's "
        f"argv[0] resolves to a real binary. Found: {argv[0]!r}"
    )


@pytest.mark.parametrize("blueprint,command", WEB_COMMANDS)
def test_docker_command_expands_port_from_environment(blueprint: str, command: str):
    """PORT must stay unexpanded so Render's runtime substitutes the real port.

    Render injects a random PORT and a health check targets it. A hardcoded
    port would pass the build and then fail every probe.
    """
    assert "${PORT" in command or "$PORT" in command, (
        f"{blueprint}: dockerCommand must read PORT from the environment so "
        f"Render's assigned port is honoured. Found: {command!r}"
    )


@pytest.mark.parametrize("blueprint,command", WEB_COMMANDS)
def test_docker_command_binds_all_interfaces(blueprint: str, command: str):
    """Render routes traffic to the container, so it must listen on 0.0.0.0."""
    assert "0.0.0.0" in command, (
        f"{blueprint}: uvicorn must bind 0.0.0.0 to be reachable by Render's "
        f"router. Found: {command!r}"
    )


@pytest.mark.parametrize("blueprint,command", WORKER_COMMANDS)
def test_worker_command_is_not_double_quoted(blueprint: str, command: str):
    """The worker command carries the same Render argv-splitting constraint."""
    assert not command.startswith("/bin/sh -c"), (
        f"{blueprint}: worker dockerCommand starts with a /bin/sh -c wrapper, "
        f"which Render will not word-split. Found: {command!r}"
    )


@pytest.mark.parametrize("blueprint,command", WORKER_COMMANDS)
def test_worker_command_runs_the_rq_worker(blueprint: str, command: str):
    """A worker service must actually run the RQ worker entrypoint."""
    argv = shlex.split(command)
    assert argv[:4] == ["python", "-m", "workers.rq_worker"], (
        f"{blueprint}: worker dockerCommand must invoke `python -m "
        f"workers.rq_worker`. Found: {argv!r}"
    )


@pytest.mark.parametrize("blueprint,command", WEB_COMMANDS)
def test_docker_command_avoids_render_pre_substitution(blueprint: str, command: str):
    """No `${...}` in dockerCommand: Render pre-substitutes it and corrupts the string.

    Render resolves `${PORT` before the container shell ever sees the command.
    Shell default syntax `${PORT:-10000}` is therefore mangled into
    `10000:-10000}`, which uvicorn rejects:

        Error: Invalid value for '--port': '10000:-10000}' is not a valid integer.

    `$PORT` is substituted correctly, and Render always sets PORT on a web
    service, so the shell default is unnecessary.
    """
    assert "${" not in command, (
        f"{blueprint}: dockerCommand must not use ${{...}} syntax. Render "
        f"pre-substitutes it and leaves the remainder, breaking the command. "
        f"Use $PORT instead. Found: {command!r}"
    )


@pytest.mark.parametrize("blueprint,command", WEB_COMMANDS)
def test_docker_command_port_value_is_a_clean_token(blueprint: str, command: str):
    """The token after --port must expand to a bare integer."""
    argv = shlex.split(command)
    port_token = argv[argv.index("--port") + 1]

    assert ":" not in port_token and "}" not in port_token, (
        f"{blueprint}: --port value {port_token!r} would expand to something "
        f"other than a plain integer"
    )
