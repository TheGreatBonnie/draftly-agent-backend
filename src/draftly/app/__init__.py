"""
Draftly application package.

The app package contains application/runtime concerns:

- configuration
- dependency injection
- application lifecycle
- HTTP API
- middleware
- background workers

Business logic belongs in domain/, workflows/, agents/, tools/,
memory/, evaluation/, and related packages.
"""

__all__ = [
    "config",
    "dependencies",
    "lifecycle",
]
