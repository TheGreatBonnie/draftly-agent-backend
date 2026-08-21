"""Documentation repository interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DocumentationRepository(ABC):
    """Interface for documentation persistence repositories."""

    @abstractmethod
    async def create(self, document: Any) -> Any:
        """Create a document record."""

    @abstractmethod
    async def get(self, document_id: str) -> Any | None:
        """Fetch a document by id."""

    @abstractmethod
    async def update(self, document_id: str, **fields: Any) -> Any | None:
        """Update a document record."""

    @abstractmethod
    async def delete(self, document_id: str) -> bool:
        """Delete a document record."""
