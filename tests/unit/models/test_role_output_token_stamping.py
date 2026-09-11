"""RoleAwareModelResolver must NOT stamp per-role output-token caps.

Model output-token limits were removed wholesale (they truncated structured
output mid-tool-use and raised MaxTokensReachedException), so role
resolution must preserve the model's configured ``max_tokens`` unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from draftly.integrations.strands.models import RoleAwareModelResolver
from draftly.models.config import ModelConfig
from draftly.models.schemas import RoutingDecision


@dataclass
class CapturingProvider:
    captured: ModelConfig | None = None
    name: str = "fake"

    def create_model(self, config: ModelConfig) -> str:
        self.captured = config
        return "concrete-model"

    def is_enabled(self) -> bool:
        return True


@dataclass
class FakeRegistry:
    provider: CapturingProvider

    def get_model(self, name: str) -> ModelConfig:
        return ModelConfig(name=name, provider="fake", model_id="fake/slug")

    def get_provider(self, name: str) -> CapturingProvider:
        return self.provider


@dataclass
class FakeRouter:
    registry: FakeRegistry

    def route(self, request) -> RoutingDecision:
        return RoutingDecision(
            selected_model="fake-reasoning",
            provider="fake",
            score=1.0,
            candidates_considered=1,
            profile="default",
        )


@pytest.fixture
def captured_provider() -> CapturingProvider:
    return CapturingProvider()


@pytest.fixture
def resolver(captured_provider: CapturingProvider) -> RoleAwareModelResolver:
    return RoleAwareModelResolver(FakeRouter(FakeRegistry(captured_provider)))


class TestRoleOutputTokenCapsRemoved:
    @pytest.mark.parametrize(
        "role",
        [
            "documentation_engineer",
            "memory_curator",
            "classifier",
            "context",
            "support_engineer",
            "github_intelligence",
        ],
    )
    def test_for_role_preserves_uncapped_model(
        self,
        resolver: RoleAwareModelResolver,
        captured_provider: CapturingProvider,
        role: str,
    ) -> None:
        resolver.for_role(role)
        model = captured_provider.captured

        assert model is not None
        assert model.max_tokens is None

    def test_unknown_role_raises_value_error(
        self,
        resolver: RoleAwareModelResolver,
    ) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            resolver.for_role("nonexistent_role")
