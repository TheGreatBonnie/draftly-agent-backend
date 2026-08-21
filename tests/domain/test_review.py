"""§8.9 verification: review service with repository fakes (§8.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from draftly.review import ReviewPolicy, ReviewService
from draftly.review.models import ReviewDecision


@dataclass
class FakeReviewRecord:
    id: str
    org_id: str = "org-1"
    thread_id: str = "run-1"
    workflow: str = "github_pr"
    tool_name: str = "doc-review"
    tool_args: dict = field(default_factory=dict)
    status: str = "pending"
    decision: str | None = None
    decision_comment: str | None = None
    reviewer_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


class FakeReviewsRepository:
    def __init__(self):
        self.records: dict[str, FakeReviewRecord] = {}

    def seed(self, record: FakeReviewRecord) -> FakeReviewRecord:
        self.records[record.id] = record
        return record

    async def get_review(self, review_id):
        return self.records.get(review_id)

    async def list_reviews(self, *, status=None, org_id=None, limit=100):
        found = [
            r for r in self.records.values()
            if (status is None or r.status == status)
            and (org_id is None or r.org_id == org_id)
        ]
        return sorted(found, key=lambda r: r.created_at)[:limit]

    async def record_decision(self, *, review_id, reviewer_id, decision, comment=None):
        record = self.records.get(review_id)
        if not record:
            raise ValueError(f"Review {review_id} not found")
        if record.status != "pending":
            return record
        record.status = decision
        record.decision = decision
        record.reviewer_id = reviewer_id
        record.decision_comment = comment
        return record

    async def expire_old_reviews(self):
        now = datetime.now(UTC)
        expired = []
        for record in self.records.values():
            if record.status == "pending" and record.expires_at and record.expires_at < now:
                record.status = "expired"
                record.decision = "timeout"
                expired.append(record)
        return expired


def make_service() -> tuple[ReviewService, FakeReviewsRepository]:
    repo = FakeReviewsRepository()
    return ReviewService(repo), repo


def seeded_record(**overrides) -> FakeReviewRecord:
    defaults = dict(
        id="rev-1",
        tool_args={
            "interrupt_id": "v1:before_node_call:deliver:doc-review",
            "reason": {"summary": "update retries doc", "evidence_count": 3},
        },
        metadata={"run_id": "run-1", "interrupt_id": "v1:interrupt"},
        expires_at=datetime.now(UTC) + __import__("datetime").timedelta(hours=24),
    )
    defaults.update(overrides)
    return FakeReviewRecord(**defaults)


class TestReviewPolicies:
    def test_policy_names_resolve(self) -> None:
        assert ReviewPolicy("always").requires_review({}) is True
        assert ReviewPolicy("never").requires_review({}) is False

    def test_risky_policy_gates_on_classification(self) -> None:
        policy = ReviewPolicy("risky")
        assert policy.requires_review({"change_type": "breaking_change"}) is True
        assert policy.requires_review({"urgency": "high"}) is True
        assert policy.requires_review({"change_type": "append"}) is False


class TestReviewQueueAndDecisions:
    async def test_pending_listing_maps_to_request_model(self) -> None:
        service, repo = make_service()
        repo.seed(seeded_record())

        pending = await service.list_pending()

        assert len(pending) == 1
        request = pending[0]
        assert request.review_id == "rev-1"
        assert request.run_id == "run-1"
        assert request.summary == "update retries doc"
        assert request.evidence_count == 3
        assert request.interrupt_id == "v1:before_node_call:deliver:doc-review"

    async def test_approval_returns_resume_payload(self) -> None:
        service, _ = make_service()
        service.repository.seed(seeded_record())

        result = await service.decide(
            ReviewDecision(
                review_id="rev-1", reviewer_id="alice", approved=True,
                comment="lgtm",
            )
        )

        resume = result["resume"]
        assert resume["response"] == {"approved": True, "comment": "lgtm"}
        assert resume["interrupt_id"].endswith("doc-review")
        record = await service.repository.get_review("rev-1")
        assert record.status == "approved"

    async def test_rejection_records_and_flags_cancellation(self) -> None:
        service, _ = make_service()
        service.repository.seed(seeded_record())

        result = await service.decide(
            ReviewDecision(
                review_id="rev-1", reviewer_id="bob", approved=False,
                comment="wrong approach",
            )
        )

        assert result["expected_outcome"] == "delivery_cancelled"
        assert result["resume"]["response"]["approved"] is False
        record = await service.repository.get_review("rev-1")
        assert record.status == "rejected"

    async def test_expiry_marks_stale_reviews(self) -> None:
        from datetime import timedelta

        service, repo = make_service()
        repo.seed(
            seeded_record(
                id="stale",
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        repo.seed(seeded_record(id="fresh"))

        count = await service.expire_stale()
        assert count == 1
        assert (await repo.get_review("stale")).status == "expired"
        assert (await repo.get_review("fresh")).status == "pending"


class TestResumeInputFormat:
    def test_build_resume_input_matches_sdk_contract(self) -> None:
        from draftly.review.approvals import ApprovalHandler

        handler = ApprovalHandler(repository=FakeReviewsRepository())
        payload = handler.build_resume_input(
            "v1:before_node_call:deliver:doc-review",
            {"approved": True, "comment": ""},
        )
        assert payload == [
            {
                "interruptResponse": {
                    "interruptId": "v1:before_node_call:deliver:doc-review",
                    "response": {"approved": True, "comment": ""},
                }
            }
        ]
