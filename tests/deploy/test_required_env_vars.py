"""Env vars without which the API (and the worker) cannot start.

These are not optional integrations. `create_application()` ->
`build_dependencies()` constructs them unconditionally during startup, so a
missing value raises and the process exits **before** the server binds its
port. Render then reports a crash-looping deploy with no `/health` at all,
which is hard to diagnose without knowing the exact variable.

Verified by running the built image and removing one variable at a time:

* ``GITHUB_TOKEN``       -> RuntimeError: GITHUB_TOKEN is not configured.
                            `build_integrations()` always builds `GitHubClient`,
                            and `GitHubClient.__init__` falls back to
                            `GitHubAuth()` when no installation id is set.
                            The installation id is a ContextVar that is `None`
                            outside a workflow run.
* ``DATABASE_URL``       -> ConnectionRefusedError; the DatabaseClient connects
                            in `start()`.
* provider API keys      -> "No healthy Draftly model was available." The router
                            resolves `fast`/`reasoning`/`research` models at
                            startup, and `create_model()` raises when its key is
                            absent.

Slack and Discord are guarded (`if settings.slack_bot_token`), so they are
deliberately absent from the required set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Vars whose absence prevents the process from starting.
MANDATORY = (
    "DATABASE_URL",
    "GITHUB_TOKEN",
    "REDIS_URL",
    "CLERK_PUBLISHABLE_KEY",
)


def _web_services(blueprint: str) -> list[dict]:
    doc = yaml.safe_load((REPO_ROOT / blueprint).read_text())
    return [s for s in doc["services"] if s["type"] == "web"]


def _application_services(blueprint: str) -> list[dict]:
    """Return services that build the Draftly application container."""
    doc = yaml.safe_load((REPO_ROOT / blueprint).read_text())
    return [s for s in doc["services"] if s["type"] in {"web", "worker"}]


@pytest.mark.parametrize("blueprint", ["render.yaml", "render.free.yaml"])
@pytest.mark.parametrize("key", MANDATORY)
def test_web_service_declares_mandatory_var(blueprint: str, key: str):
    """The Blueprint must request every startup-mandatory variable."""
    for service in _web_services(blueprint):
        declared = {e["key"] for e in service.get("envVars", [])}
        assert key in declared, (
            f"{blueprint}/{service['name']}: {key} is not declared. Without it "
            f"the app raises during startup and Render sees a crash loop, not a "
            f"failing health check."
        )


@pytest.mark.parametrize("blueprint", ["render.yaml", "render.free.yaml"])
def test_mandatory_vars_are_not_hardcoded(blueprint: str):
    """Mandatory credentials must be `sync: false`, never inline values."""
    for service in _web_services(blueprint):
        for entry in service.get("envVars", []):
            if entry["key"] in MANDATORY:
                assert "value" not in entry, (
                    f"{blueprint}/{service['name']}: {entry['key']} must be "
                    f"`sync: false`, not an inline value in a public repo"
                )


@pytest.mark.parametrize("blueprint", ["render.yaml", "render.free.yaml"])
def test_application_services_receive_the_github_app_key_inline(blueprint: str):
    """Containers without a secret-file mount must receive PEM material inline."""
    for service in _application_services(blueprint):
        entries = {entry["key"]: entry for entry in service.get("envVars", [])}
        assert entries.get("GITHUB_APP_PRIVATE_KEY") == {
            "key": "GITHUB_APP_PRIVATE_KEY",
            "sync": False,
        }, f"{blueprint}/{service['name']}: inline GitHub App PEM is not requested"
        assert "GITHUB_PRIVATE_KEY_PATH" not in entries, (
            f"{blueprint}/{service['name']}: declares a PEM path but mounts no secret file"
        )


def test_requesty_is_optional():
    """Requesty must NOT be required: the stage models fall back.

    factory.py pins stage-research/stage-review/stage-rubric-grader to
    provider="requesty". When Requesty is excluded from
    DRAFTLY_ENABLED_PROVIDERS, ModelRouter.resolve_model now selects by the
    model's capabilities instead of raising, so an operator can run without
    Requesty. This test locks that the Blueprints do not reintroduce the key as
    a hard requirement.
    """
    for blueprint in ("render.yaml", "render.free.yaml"):
        for service in _web_services(blueprint):
            declared = {e["key"] for e in service.get("envVars", [])}
            assert "REQUESTY_API_KEY" not in declared, (
                f"{blueprint}/{service['name']}: REQUESTY_API_KEY should not be "
                f"required now that resolve_model falls back by capability"
            )
