"""Unit tests for Document memory model."""

from draftly.memory.models.document import Document


def test_document_has_heading_fields():
    doc = Document(
        namespace="documents",
        content="test",
        heading_path="A > B",
        start_line=5,
        end_line=10,
    )
    assert doc.heading_path == "A > B"
    assert doc.start_line == 5
    assert doc.end_line == 10


def test_document_heading_fields_optional():
    doc = Document(
        namespace="documents",
        content="test",
    )
    assert doc.heading_path is None
    assert doc.start_line is None
    assert doc.end_line is None
