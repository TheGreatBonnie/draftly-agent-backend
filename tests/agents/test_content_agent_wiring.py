from draftly.agents.content.blog_writer import build_blog_writer
from draftly.agents.content.schemas import (
    ContentBriefOutput,
    ContentDraftOutput,
    ContentSocialOutput,
)
from draftly.agents.content.social_adapter import build_social_adapter
from draftly.agents.content.strategist import build_content_strategist
from draftly.app.composition.tools import build_tools
from tests.stub_model import StubModel


def test_content_builders_create_real_strands_agents_with_contracts():
    model = StubModel()
    tools = [lambda: None]

    strategist = build_content_strategist(model, tools)
    blog_writer = build_blog_writer(model, tools)
    social_adapter = build_social_adapter(model, tools)

    assert strategist.__class__.__name__ == "Agent"
    assert blog_writer.__class__.__name__ == "Agent"
    assert social_adapter.__class__.__name__ == "Agent"
    assert strategist._default_structured_output_model is ContentBriefOutput
    assert blog_writer._default_structured_output_model is ContentDraftOutput
    assert social_adapter._default_structured_output_model is ContentSocialOutput
    assert strategist.tool_registry is not None


def test_content_tools_are_a_first_class_scoped_registry_group():
    registry = build_tools()

    assert registry.content
    assert set(registry.content).issubset(set(registry.all_tools))
