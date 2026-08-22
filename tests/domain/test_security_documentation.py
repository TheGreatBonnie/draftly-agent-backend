"""§8.9 verification: security primitives (§8.8) + documentation
domain services (§8.4)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from draftly.documentation import (
    DocumentationAnalyzer,
    DocumentationGenerator,
    DocumentationValidator,
)
from draftly.security import (
    PermissionChecker,
    PermissionDeniedError,
    Principal,
    RedactionService,
    WebhookVerifier,
)

# ================================================================
# Webhook verification (§8.8)
# ================================================================


class TestWebhookVerification:
    SECRET = "whsec_test"

    def test_github_valid_signature(self) -> None:
        verifier = WebhookVerifier(github_secret=self.SECRET)
        body = b'{"action": "opened"}'
        digest = hmac.new(self.SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verifier.verify_github(body, f"sha256={digest}") is True

    def test_github_tampered_signature_rejected(self) -> None:
        verifier = WebhookVerifier(github_secret=self.SECRET)
        assert verifier.verify_github(b"{}", "sha256=" + "0" * 64) is False

    def test_github_missing_secret_raises(self) -> None:
        with pytest.raises(Exception, match="not configured"):
            WebhookVerifier().verify_github(b"{}", "sha256=x")

    def test_slack_valid_signature_with_replay_window(self) -> None:
        verifier = WebhookVerifier(slack_signing_secret=self.SECRET)
        body = b"payload=1"
        timestamp = str(int(time.time()))
        basestring = f"v0:{timestamp}:".encode() + body
        digest = hmac.new(self.SECRET.encode(), basestring, hashlib.sha256).hexdigest()
        assert verifier.verify_slack(body, timestamp, f"v0={digest}") is True

    def test_slack_replayed_timestamp_rejected(self) -> None:
        verifier = WebhookVerifier(slack_signing_secret=self.SECRET)
        old_ts = str(int(time.time()) - 3600)
        assert verifier.verify_slack(b"x", old_ts, "v0=abc") is False

    def test_discord_missing_key_raises(self) -> None:
        with pytest.raises(Exception, match="not configured"):
            WebhookVerifier().verify_discord(b"{}", "123", "ff")


# ================================================================
# Redaction / permissions / secrets (§8.8)
# ================================================================


class TestRedaction:
    def test_redacts_common_secret_shapes(self) -> None:
        redactor = RedactionService()
        text = "email me at dev@example.com, key=AKIAIOSFODNN7EXAMPLE, Authorization: Bearer abc123"
        cleaned = redactor.redact(text)
        assert "dev@example.com" not in cleaned
        assert "AKIAIOSFODNN7EXAMPLE" not in cleaned

    def test_private_key_block_detected_and_removed(self) -> None:
        redactor = RedactionService()
        text = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
        assert redactor.contains_secret(text) is True
        assert "PRIVATE KEY" not in redactor.redact(text).replace("[REDACTED_PRIVATE_KEY]", "")


class TestPermissions:
    def test_role_permissions_enforced(self) -> None:
        checker = PermissionChecker()
        admin = Principal(id="a", roles=["admin"])
        viewer = Principal(id="v", roles=["viewer"])
        assert checker.has_permission(admin, "review.approve") is True
        assert checker.has_permission(viewer, "review.approve") is False
        checker.require(admin, "review.approve")
        with pytest.raises(PermissionDeniedError):
            checker.require(viewer, "review.approve")

    def test_cross_org_access_denied(self) -> None:
        checker = PermissionChecker()
        outsider = Principal(id="x", org_id="org-2", roles=["admin"])
        with pytest.raises(PermissionDeniedError, match="cross-org"):
            checker.require(outsider, "docs.write", org_id="org-1")


# ================================================================
# Documentation domain (§8.4)
# ================================================================


class TestDocumentationDomain:
    analyzer = DocumentationAnalyzer()

    def test_topics_and_links_extraction(self) -> None:
        content = "# Setup\n\nSee [guide](docs/guide.md) and [site](https://x.y).\n## Advanced"
        assert self.analyzer.topics(content) == ["Setup", "Advanced"]
        links = self.analyzer.links(content)
        assert "docs/guide.md" in links and "https://x.y" in links

    def test_gap_detection_flags_uncovered_questions(self) -> None:
        questions = ["How do I rotate API keys?", "rotate api keys please"]
        documents = [
            {
                "title": "Backups",
                "path": "docs/backups.md",
                "content": "# Backups\nAutomated backups run daily.",
            }
        ]
        gaps = self.analyzer.detect_gaps(questions, documents)
        assert len(gaps) == 1
        assert gaps[0].occurrences == 2

    def test_validator_detects_broken_links_and_staleness(self) -> None:
        validator = DocumentationValidator(stale_after_days=30)
        result = validator.validate(
            path="docs/a.md",
            content="[missing](docs/nope.md)",
            updated_at=None,
            known_paths={"docs/other.md"},
        )
        assert result.valid is False
        assert result.broken_links == ["docs/nope.md"]

    def test_generator_renders_front_matter_and_sections(self) -> None:
        generator = DocumentationGenerator()
        content = generator.generate(
            title="Retries Guide",
            sections=[{"heading": "Config", "body": "Set retry_count."}],
            front_matter={"source": "draftly"},
        )
        assert content.startswith("---\ntitle: Retries Guide")
        assert "## Config" in content
        path = generator.build_path(repository="acme/api", title="Retries Guide")
        assert path == "docs/retries-guide.md"
