"""Unknown provider names must not degrade silently into a smaller allowlist.

``DRAFTLY_ENABLED_PROVIDERS=mantle,mantle_openai,...`` was accepted with only
a warning. ``mantle_openai`` is not a registry name (it is ``mantle-openai``),
so the gate filtered every ``mantle-openai`` model out of routing *and* out of
failover rotation, while the run looked correctly configured. Nine occurrences
of ``enabled_providers_unknown`` in one worker start were the only signal.

``_enabled_providers_from_env`` must therefore drop names it cannot honour, so
the effective allowlist is exactly the intended one and the caller cannot
mistake a typo for a configuration.
"""

from __future__ import annotations

import pytest

from draftly.models.factory import KNOWN_PROVIDERS, _enabled_providers_from_env

ENV = "DRAFTLY_ENABLED_PROVIDERS"


def _set(monkeypatch, value: str) -> None:
    monkeypatch.setenv(ENV, value)


def test_typo_is_dropped_not_merged(monkeypatch) -> None:
    """The regression: mantle_openai is not mantle-openai."""
    _set(monkeypatch, "mantle,mantle_openai,nebius_token_factory")

    resolved = _enabled_providers_from_env()

    assert resolved == {"mantle", "nebius_token_factory"}
    assert "mantle_openai" not in resolved


def test_underscore_variant_is_not_silently_accepted(monkeypatch) -> None:
    """The gate must not 'helpfully' normalise a name it does not know.

    ``None`` means "unset => allow everything", so an allowlist of nothing but
    typos must not widen to every provider. It stays an empty allowlist, which
    ``route()`` surfaces as ``NoCandidateError`` rather than silently un-gating.
    """
    _set(monkeypatch, "mantle_openai")

    resolved = _enabled_providers_from_env()

    assert resolved is not None
    assert resolved == set()


def test_unset_allows_everything(monkeypatch) -> None:
    monkeypatch.delenv(ENV, raising=False)

    assert _enabled_providers_from_env() is None


def test_empty_string_allows_everything(monkeypatch) -> None:
    """Documented as 'empty/unset => all'; must not become an empty allowlist."""
    _set(monkeypatch, "")

    assert _enabled_providers_from_env() is None


def test_trailing_comma_is_tolerated(monkeypatch) -> None:
    _set(monkeypatch, "mantle,nebius_token_factory,")

    assert _enabled_providers_from_env() == {"mantle", "nebius_token_factory"}


def test_valid_names_pass_through_unchanged(monkeypatch) -> None:
    _set(monkeypatch, "mantle,mantle-openai,nebius_token_factory")

    assert _enabled_providers_from_env() == {
        "mantle",
        "mantle-openai",
        "nebius_token_factory",
    }


def test_whitespace_and_case_are_normalised(monkeypatch) -> None:
    _set(monkeypatch, "  MANTLE , Mantle-OpenAI  ")

    assert _enabled_providers_from_env() == {"mantle", "mantle-openai"}


def test_every_surviving_name_is_a_real_registry_provider(monkeypatch) -> None:
    """The contract: whatever comes back is routable."""
    _set(monkeypatch, "mantle,mantle-openai,requesty,orcarouter,nvidia,openrouter,bedrock")

    resolved = _enabled_providers_from_env()

    assert resolved is not None
    assert resolved <= set(KNOWN_PROVIDERS)


def test_warning_still_names_the_offenders(monkeypatch) -> None:
    """Dropping the names must not lose the diagnostic that explained it."""
    from draftly.models import factory as factory_module

    seen: dict = {}
    original = factory_module.logger

    class _Capture:
        def warning(self, event, *args, **kwargs):
            seen[event] = {"template": event, **kwargs}

        def __getattr__(self, _name):
            return lambda *a, **k: None

    factory_module.logger = _Capture()
    try:
        _set(monkeypatch, "mantle,typo_one,typo_two")
        _enabled_providers_from_env()
    finally:
        factory_module.logger = original

    assert "enabled_providers_unknown" in seen
    captured = seen["enabled_providers_unknown"]
    assert sorted(captured["providers"]) == ["typo_one", "typo_two"]
