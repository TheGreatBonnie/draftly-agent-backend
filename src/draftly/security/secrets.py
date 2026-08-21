"""Secret management (plan §8.8) — env-first resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretRef:
    """Reference to a secret held outside the codebase."""

    name: str
    env_var: str | None = None

    def resolve(self) -> str | None:
        return os.getenv(self.env_var or self.name)


class SecretManager:
    """Resolve secrets from the environment (never log values)."""

    def __init__(self, prefix: str = "DRAFTLY_") -> None:
        self.prefix = prefix

    def get(self, name: str) -> str | None:
        """Look up DRAFTLY_<NAME> then bare <NAME>."""
        prefixed = os.getenv(f"{self.prefix}{name.upper()}")
        if prefixed is not None:
            return prefixed
        return os.getenv(name.upper())

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise KeyError(
                f"missing secret '{name}' "
                f"(looked for {self.prefix}{name.upper()} and {name.upper()})"
            )
        return value

    def ref(self, name: str) -> SecretRef:
        return SecretRef(name=name, env_var=f"{self.prefix}{name.upper()}")
