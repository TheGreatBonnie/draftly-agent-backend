"""DRAFTLY_ENABLED_PROVIDERS must actually restrict which providers resolve.

Regression: `build_model_router` reads the allowlist and hands it to
`ModelRouter(enabled_providers=...)`, and `_enabled_providers_from_env` even
logs a warning for unrecognised names. But `ModelRouter.resolve()` -- the
startup path used by `build_models` -- filters candidates only by capability:

    candidates = self.registry.list_models()
    candidates = [m for m in candidates if self._supports_capabilities(m, policy)]

It never consults `_enabled_providers`, so every registered provider stays
routable. `ProviderConfig.enabled` defaults to `True` and nothing ever
overwrites it from the allowlist, which makes `provider.is_enabled()` -- the
only gate `resolve()` does check -- permanently true.

Observed consequence: with DRAFTLY_ENABLED_PROVIDERS set to the five demo
providers, startup still attempted `nebius_token_factory` and died with
"No healthy Draftly model was available." An operator cannot turn a provider
off, and a disabled provider's missing key is indistinguishable from a real
outage.

The allowlist only reached the newer scoring path (`score()`), which is not
what `build_models` calls. These tests lock the contract that resolve() honours
it too.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from draftly.models.policies import KNOWN_PROVIDERS


def _build_router(enabled: set[str] | None):
    """Build a router with only api-key-free providers registered."""
    from draftly.models.factory import build_model_router

    with patch.dict(os.environ, {"DRAFTLY_ENABLED_PROVIDERS": "openrouter"}, clear=False):
        return build_model_router(enabled_providers=enabled)


def test_resolve_never_returns_a_disabled_provider():
    """A provider outside the allowlist must not be routable."""
    router = _build_router({"openrouter"})

    attempted: list[str] = []
    for model in router.registry.list_models():
        provider = router.registry.get_provider(model.provider)
        if provider.is_enabled():
            attempted.append(model.provider)

    assert set(attempted) <= {"openrouter"}, (
        "resolve() is selecting providers outside DRAFTLY_ENABLED_PROVIDERS. "
        f"Disallowed providers still report is_enabled(): {sorted(set(attempted))}"
    )


def test_disabled_provider_config_is_marked_not_enabled():
    """register_provider must apply the allowlist to ProviderConfig.enabled.

    This is the mechanism that makes `provider.is_enabled()` -- the only gate
    resolve() consults -- actually reflect DRAFTLY_ENABLED_PROVIDERS.
    """

    router = _build_router({"openrouter"})
    orca = router.registry.get_provider("orcarouter")

    assert orca.config.enabled is False, (
        "ProviderConfig.enabled is still True for a provider excluded by "
        "DRAFTLY_ENABLED_PROVIDERS, so the allowlist cannot restrict routing"
    )


def test_allowlist_is_not_silently_ignored_for_known_provider():
    """A known-but-unlisted provider must be filtered out of the allowlist."""
    from draftly.models.factory import _enabled_providers_from_env

    with patch.dict(
        os.environ,
        {"DRAFTLY_ENABLED_PROVIDERS": "openrouter,nvidia"},
        clear=False,
    ):
        allowed = _enabled_providers_from_env()

    assert allowed == {"openrouter", "nvidia"}
    assert "orcarouter" not in allowed
    assert allowed <= KNOWN_PROVIDERS


def test_unset_allowlist_still_enables_every_provider():
    """Omitting the variable must not disable anything (back-compat)."""
    from draftly.models.factory import _enabled_providers_from_env

    with patch.dict(os.environ, {}, clear=True):
        assert _enabled_providers_from_env() is None


def test_provider_config_enabled_defaults_true_for_direct_construction():
    """Documents the default that makes the bug invisible without an allowlist."""
    from draftly.models.providers.base import ProviderConfig

    assert ProviderConfig(name="x", api_key=None, base_url=None).enabled is True

def test_resolve_model_never_instantiates_a_disabled_provider():
    """A disabled pinned provider must not be instantiated.

    `build_models` resolves the stage models by name, and factory.py pins all
    three to `provider="requesty"`. The lookup must not reach `create_model()`
    on a disabled provider, otherwise startup dies with "REQUESTY_API_KEY is
    not configured." even though Requesty was excluded.

    mantle supplies the `research` capability, so it is the only enabled
    candidate here and the fallback must land on it.
    """
    with patch.dict(os.environ, {"MANTLE_API_KEY": "sk-test"}, clear=False):
        router = _build_router({"mantle", "openrouter"})

        model = router.resolve_model("stage-research")

    assert model is not None
    assert not router.registry.get_provider("requesty").is_enabled(), (
        "requesty must be disabled in this configuration, so stage-research "
        "must have been served by the capability fallback"
    )


def test_resolve_model_allows_an_enabled_provider():
    """Sanity: the new guard must not block a permitted provider.

    `stage-research` resolves to a Requesty model by default, so Requesty must
    be allowed *and* keyed for create_model() to succeed.
    """
    with patch.dict(os.environ, {"REQUESTY_API_KEY": "sk-test"}, clear=False):
        router = _build_router({"openrouter", "requesty"})

        model = router.resolve_model("stage-research")

    assert model is not None


def test_resolve_model_falls_back_when_pinned_provider_is_disabled():
    """A disabled pinned provider must fall back to a capability match.

    `build_models` resolves the three stage models by name, and factory.py
    pins all three to `provider="requesty"`. With Requesty excluded from
    DRAFTLY_ENABLED_PROVIDERS that lookup has no enabled candidate, so the
    router must select by the model's declared capabilities instead of raising
    -- otherwise a provider the operator disabled takes the whole deploy down.

    Every capability involved is covered without Requesty: `research` by
    mantle, `verification` by orcarouter/mantle, `evaluation` by orcarouter.
    """
    keys = {
        "MANTLE_API_KEY": "sk-test",
        "ORCAROUTER_API_KEY": "sk-test",
        "NVIDIA_API_KEY": "sk-test",
        "OPENROUTER_API_KEY": "sk-test",
    }
    with patch.dict(os.environ, keys, clear=False):
        router = _build_router(
            {"mantle", "mantle-openai", "orcarouter", "nvidia", "openrouter"}
        )

    for stage in ("stage-research", "stage-review", "stage-rubric-grader"):
        model = router.resolve_model(stage)
        assert model is not None, f"{stage} should fall back to an enabled provider"
        assert "requesty" not in repr(model).lower()


def test_resolve_model_prefers_the_pinned_provider_when_enabled():
    """Back-compat: with requesty enabled the pinned model still wins."""
    with patch.dict(os.environ, {"REQUESTY_API_KEY": "sk-test"}, clear=False):
        router = _build_router({"openrouter", "requesty"})
        model = router.resolve_model("stage-research")

    config = router.registry.get_model("stage-research")
    assert router.registry.get_provider(config.provider).is_enabled()
    assert model is not None
