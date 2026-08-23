"""Tests for Nvidia provider and removal of its EOL models."""

from __future__ import annotations


class TestBuildModelRouterWithNvidia:
    """Nvidia models reached end-of-life (410 Gone) on 2026-08-21."""

    def test_build_model_router_registers_nvidia_provider(self) -> None:
        """The provider stays registered for future model additions."""
        import os

        os.environ["NVIDIA_API_KEY"] = "test-key"

        from draftly.models.factory import build_model_router

        router = build_model_router()

        assert "nvidia" in router.registry.providers()

    def test_build_model_router_has_no_nvidia_models(self) -> None:
        """Live probe: all nvidia slugs are EOL (410) or absent (404).

        Registered during the probe-all phase, then pruned by evidence.
        """
        import os

        os.environ["NVIDIA_API_KEY"] = "test-key"

        from draftly.models.factory import build_model_router

        router = build_model_router()

        assert [m for m in router.registry.list_models() if m.provider == "nvidia"] == []
