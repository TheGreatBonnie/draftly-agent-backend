from __future__ import annotations

import json
from pathlib import Path

from draftly.events.github.pull_request import PullRequestProcessor

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


def closed_pr_payload(*, merged: bool) -> dict:
    return {
        "action": "closed",
        "delivery_id": "d-merged",
        "repository": {"full_name": "acme/api"},
        "sender": {"login": "dev"},
        "pull_request": {
            "number": 7,
            "title": "Fix widget",
            "state": "closed",
            "merged": merged,
            "head": {"sha": "abc"},
            "base": {"ref": "main"},
        },
    }


async def test_closed_merged_emits_pull_request_merged() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(closed_pr_payload(merged=True))
    assert event.event_type == "pull_request.merged"
    assert event.pull_request["action"] == "merged"


async def test_closed_not_merged_emits_pull_request_closed() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(closed_pr_payload(merged=False))
    assert event.event_type == "pull_request.closed"
    assert event.pull_request["action"] == "closed"


async def test_opened_emits_pull_request_opened() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api"},
            "pull_request": {"number": 8, "head": {"sha": "x"}, "base": {"ref": "main"}},
        }
    )
    assert event.event_type == "pull_request.opened"
    assert event.pull_request["action"] == "opened"


def pr_payload_with_evidence() -> dict:
    return {
        "action": "opened",
        "delivery_id": "d-evidence",
        "repository": {"full_name": "acme/api"},
        "sender": {"login": "dev"},
        "pull_request": {
            "number": 9,
            "title": "OAuth login",
            "state": "open",
            "merged": False,
            "head": {"ref": "feat/oauth", "sha": "b0d7fb8"},
            "base": {"ref": "master", "sha": "9a8581c"},
            "html_url": "https://github.com/acme/api/pull/9",
            "labels": [],
            "user": {"login": "scenario-bot"},
            "changed_files": [
                "src/api/oauth.py",
                "tests/test_oauth.py",
            ],
            "changed_file_details": [
                {
                    "path": "src/api/oauth.py",
                    "change": "Added exchange_code",
                    "action": "update",
                },
            ],
            "file_actions": {"src/api/oauth.py": "update"},
            "diff": "diff --git a/src/api/oauth.py b/src/api/oauth.py",
        },
    }


async def test_forwards_real_changed_files_into_normalized_event() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(pr_payload_with_evidence())
    assert event.pull_request["changed_files"] == [
        "src/api/oauth.py",
        "tests/test_oauth.py",
    ]
    assert event.pull_request["changed_file_details"] == [
        {
            "path": "src/api/oauth.py",
            "change": "Added exchange_code",
            "action": "update",
        },
    ]
    assert event.pull_request["file_actions"] == {"src/api/oauth.py": "update"}
    assert event.pull_request["diff"] == "diff --git a/src/api/oauth.py b/src/api/oauth.py"


async def test_forwards_head_ref_for_source_pr_delivery() -> None:
    """The source PR's head branch must reach the delivery agent so approved
    docs + changelog commits land on the PR that triggered the workflow."""
    processor = PullRequestProcessor()
    event = await processor.process(pr_payload_with_evidence())
    head = event.pull_request["head"]
    assert head == {"ref": "feat/oauth", "sha": "b0d7fb8"}


async def test_absent_evidence_fields_stay_absent() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api"},
            "pull_request": {"number": 10, "head": {"sha": "x"}, "base": {"ref": "main"}},
        }
    )
    assert "changed_files" not in event.pull_request
    assert "changed_file_details" not in event.pull_request
    assert "file_actions" not in event.pull_request
    assert "diff" not in event.pull_request


async def test_empty_evidence_lists_are_omitted() -> None:
    processor = PullRequestProcessor()
    event = await processor.process(
        {
            "action": "opened",
            "repository": {"full_name": "acme/api"},
            "pull_request": {
                "number": 11,
                "head": {"sha": "x"},
                "base": {"ref": "main"},
                "changed_files": [],
                "changed_file_details": [],
                "file_actions": {},
                "diff": "",
            },
        }
    )
    assert "changed_files" not in event.pull_request
    assert "changed_file_details" not in event.pull_request
    assert "file_actions" not in event.pull_request
    assert "diff" not in event.pull_request


async def test_committed_crafted_payload_carries_real_changed_files() -> None:
    payload = json.loads((SCRIPTS_DIR / "pr_opened.json").read_text())
    event = await PullRequestProcessor().process(payload)
    assert event.pull_request["changed_files"] == [
        ".gitignore",
        "CHANGELOG.md",
        "src/authly/auth.py",
        "src/authly/oauth.py",
        "tests/test_auth.py",
        "tests/test_oauth.py",
    ]
    assert event.pull_request["file_actions"]["src/authly/oauth.py"] == "update"
    assert event.pull_request["changed_file_details"][0] == {
        "path": ".gitignore",
        "change": "Ignored generated files",
        "action": "update",
    }


async def test_normalization_summary_is_logged(monkeypatch) -> None:
    """The normalizer currently logs nothing — a missing diff or a silently
    dropped evidence field is invisible until the run misbehaves downstream."""
    import structlog
    from structlog.testing import capture_logs

    import draftly.events.github.pull_request as pull_request_module

    payload = {
        "action": "opened",
        "delivery_id": "d-log",
        "repository": {"full_name": "acme/api"},
        "sender": {"login": "dev"},
        "pull_request": {
            "number": 7,
            "title": "Fix widget",
            "state": "open",
            "head": {"sha": "abc"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/acme/api/pull/7",
            "changed_files": ["src/authly/oauth.py", "tests/test_oauth.py"],
            "diff": "diff --git a/src/authly/oauth.py b/src/authly/oauth.py",
        },
    }

    with capture_logs() as logs:
        monkeypatch.setattr(
            pull_request_module,
            "logger",
            structlog.get_logger("test.pull_request.normalized"),
        )
        await PullRequestProcessor().process(payload)

    markers = [line for line in logs if line.get("event") == "github_pr_normalized"]
    assert len(markers) == 1
    assert markers[0]["action"] == "opened"
    assert markers[0]["repository"] == "acme/api"
    assert markers[0]["actor"] == "dev"
    assert markers[0]["changed_files"] == 2
    assert markers[0]["has_diff"] is True
