"""Permission checks (plan §8.8)."""

from __future__ import annotations

from dataclasses import dataclass, field

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "review.approve",
        "review.reject",
        "docs.write",
        "docs.delete",
        "delivery.github",
        "memory.write",
        "settings.read",
    },
    "maintainer": {
        "review.approve",
        "review.reject",
        "docs.write",
        "delivery.github",
        "memory.write",
    },
    "member": {
        "docs.write",
        "memory.write",
    },
    "viewer": set(),
}


@dataclass
class Principal:
    """An authenticated actor."""

    id: str
    org_id: str | None = None
    roles: list[str] = field(default_factory=list)

    @property
    def permissions(self) -> set[str]:
        granted: set[str] = set()
        for role in self.roles:
            granted |= ROLE_PERMISSIONS.get(role, set())
        return granted


class PermissionDeniedError(PermissionError):
    """Raised when a principal lacks a required permission."""


class PermissionChecker:
    """Coarse-grained RBAC checks (org-scoped)."""

    def has_permission(self, principal: Principal, permission: str) -> bool:
        return permission in principal.permissions

    def require(
        self,
        principal: Principal,
        permission: str,
        *,
        org_id: str | None = None,
    ) -> None:
        if org_id and principal.org_id and principal.org_id != org_id:
            raise PermissionDeniedError(f"cross-org access denied for {principal.id}")
        if not self.has_permission(principal, permission):
            raise PermissionDeniedError(f"{principal.id} lacks permission '{permission}'")
