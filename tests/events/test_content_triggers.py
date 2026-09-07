import pytest

from draftly.app.composition.events import build_event_system


@pytest.mark.asyncio
async def test_release_is_content_eligible_only_when_published():
    system = build_event_system()
    event = await system.normalize_github({
        "action": "published", "release": {"id": 1, "name": "1.0", "html_url": "url"},
        "repository": {"full_name": "org/repo"},
    })
    assert system.workflow_type_for(event) == "content"
    assert system.content_source_for(event)["source_event_type"] == "release"


@pytest.mark.asyncio
async def test_unmarked_merged_pr_is_not_content_eligible():
    system = build_event_system()
    event = await system.normalize_github({
        "action": "closed", "pull_request": {
            "id": 1, "number": 1, "merged": True, "title": "Fix",
            "labels": [], "head": {}, "base": {},
        }, "repository": {"full_name": "org/repo"},
    })
    assert system.content_source_for(event) is None
