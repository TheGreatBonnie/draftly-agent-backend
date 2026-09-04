"""RoleAwareModelResolver must stamp ROLE_OUTPUT_TOKENS onto the model."""

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


class TestRoleOutputTokenStamping:
    @pytest.mark.parametrize(
        ("role", "expected_tokens"),
        [
            ("documentation_engineer", 8192),
            ("memory_curator", 1024),
            ("classifier", 1024),
            ("context", 2048),
            ("support_engineer", 2048),
            ("github_intelligence", 2048),
        ],
    )
    def test_for_role_stamps_output_tokens(
        self,
        resolver: RoleAwareModelResolver,
        captured_provider: CapturingProvider,
        role: str,
        expected_tokens: int,
    ) -> None:
        resolver.for_role(role)

        assert captured_provider.captured is not None
        assert captured_provider.captured.max_tokens == expected_tokens

    def test_unknown_role_raises_value_error(
        self,
        resolver: RoleAwareModelResolver,
    ) -> None:
        with pytest.raises(ValueError, match="Unknown role"):
            resolver.for_role("nonexistent_role")
