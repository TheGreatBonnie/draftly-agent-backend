from draftly.agents.content.blog_writer import BlogWriter
from draftly.agents.content.social_adapter import SocialAdapter
from draftly.agents.content.strategist import ContentStrategist


def test_content_agents_preserve_evidence_and_do_not_truncate_x():
    evidence = [{"source_id": "doc-1"}]
    brief = ContentStrategist().build_brief(title="Release", summary="Update", evidence=evidence)
    blog = BlogWriter().write(title=brief["title"], summary=brief["message"], evidence=evidence)
    post = SocialAdapter().adapt(
        title="Release", summary="x" * 500, channel="x", evidence=evidence
    )
    assert blog["evidence"] == evidence
    assert post["evidence"] == evidence
    assert len(post["body"]) > 280
