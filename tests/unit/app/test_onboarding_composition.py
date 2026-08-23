"""Composition wiring checks for onboarding repositories."""

from __future__ import annotations

import inspect

from draftly.app.dependencies import RepositoryDependencies, build_repositories


def test_repository_dependencies_declares_onboarding_fields():
    params = inspect.signature(RepositoryDependencies.__init__).parameters
    assert "onboarding" in params
    assert "repository_config" in params


def test_build_repositories_constructs_onboarding_repositories():
    src = inspect.getsource(build_repositories)
    assert "OnboardingRepository(" in src
    assert "RepositoryConfigRepository(" in src
    # Both must receive the shared database client like their siblings
    assert src.count("client=database") >= 2
