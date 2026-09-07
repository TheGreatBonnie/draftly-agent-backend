from unittest.mock import patch

from draftly.observability.workflow_logging import log_content_event


def test_content_logging_contains_metadata_without_content():
    with patch("draftly.observability.workflow_logging.logger.info") as info:
        log_content_event(
            "content_variant_generated", run_id="run-1", org_id="org-1",
            package_id="package-1", source_event_id="release-1", channel="blog", status="draft",
        )
    fields = info.call_args.kwargs
    assert fields["workflow"] == "content_generation"
    assert "body" not in fields
