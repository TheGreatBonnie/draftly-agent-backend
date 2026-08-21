"""Draftly documentation subsystem (plan §8.4).

Exports are lazy (PEP 562) because ``draftly.persistence.repositories.
documents`` imports ``draftly.documentation.repositories`` (the ABC) at
module level; eager re-exports here would create a circular import.
"""

from typing import Any

_LAZY_EXPORTS = {
    "DocumentInfo": ("draftly.documentation.models", "DocumentInfo"),
    "DocumentationAnalyzer": ("draftly.documentation.analyzer", "DocumentationAnalyzer"),
    "DocumentationGap": ("draftly.documentation.models", "DocumentationGap"),
    "DocumentationGenerator": ("draftly.documentation.generator", "DocumentationGenerator"),
    "DocumentationIndexer": ("draftly.documentation.indexer", "DocumentationIndexer"),
    "DocumentationService": ("draftly.documentation.service", "DocumentationService"),
    "DocumentationUpdater": ("draftly.documentation.updater", "DocumentationUpdater"),
    "DocumentationValidator": ("draftly.documentation.validator", "DocumentationValidator"),
    "ValidationResult": ("draftly.documentation.models", "ValidationResult"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        import importlib

        module_path, attr = _LAZY_EXPORTS[name]
        return getattr(importlib.import_module(module_path), attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
