"""Unit tests for baseline snapshot."""

from draftly.documentation.baseline import create_baseline


def test_create_baseline_captures_all_fields():
    snapshot = create_baseline(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=5,
        section_count=20,
        chunk_count=40,
        include=["*.md"],
        exclude=["node_modules/**"],
    )
    assert snapshot.commit_sha == "abc123"
    assert snapshot.repository == "owner/repo"
    assert snapshot.document_count == 5
    assert snapshot.section_count == 20
    assert snapshot.chunk_count == 40
    assert snapshot.include == ["*.md"]
    assert snapshot.exclude == ["node_modules/**"]
    assert snapshot.synced_at is not None


def test_baseline_to_dict():
    snapshot = create_baseline(
        commit_sha="abc123",
        repository="owner/repo",
        document_count=1,
        section_count=1,
        chunk_count=1,
    )
    d = snapshot.to_dict()
    assert d["commit_sha"] == "abc123"
    assert d["document_count"] == 1
    assert "synced_at" in d
