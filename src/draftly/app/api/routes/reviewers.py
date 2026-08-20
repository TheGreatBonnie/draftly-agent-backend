from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from draftly.app.api.auth import get_verified_token, require_admin_role, require_reviewer_role
from draftly.persistence.repositories.reviewers import ReviewersRepository

router = APIRouter(
    prefix="/reviewers",
    tags=["reviewers"],
)


# ── Request Models ────────────────────────────────────────────


class CreateReviewerRequest(BaseModel):
    org_id: str | None = None
    name: str
    email: str | None = None
    slack_user_id: str | None = None
    discord_user_id: str | None = None
    notify_slack: bool = True
    notify_discord: bool = False
    notify_email: bool = False


class UpdateReviewerRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    slack_user_id: str | None = None
    discord_user_id: str | None = None
    notify_slack: bool | None = None
    notify_discord: bool | None = None
    notify_email: bool | None = None
    is_active: bool | None = None


class AssignRoleRequest(BaseModel):
    user_id: str
    role: str


class SelfRegisterRequest(BaseModel):
    slack_user_id: str | None = None
    discord_user_id: str | None = None
    notify_slack: bool = True
    notify_discord: bool = False
    notify_email: bool = False


# ── Helpers ───────────────────────────────────────────────────


def _get_repo(request: Request) -> Any:
    """Lazily resolve the reviewers repository from app state."""
    application = request.app.state.draftly
    return application.dependencies.repositories.reviewers


# ── Admin: manage org member roles ────────────────────────────


@router.get("/org-members")
async def list_org_members(
    request: Request,
    token: dict = Depends(require_admin_role),
) -> dict[str, Any]:
    """List Clerk org members with their roles (admin only)."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    from draftly.integrations.clerk.client import list_org_members as clerk_list_members

    members = await clerk_list_members(org_id)
    return {"members": members}


@router.post("/assign-role")
async def assign_role(
    request: Request,
    body: AssignRoleRequest,
    token: dict = Depends(require_admin_role),
) -> dict[str, Any]:
    """Assign a role to an org member via Clerk (admin only)."""
    org_id = token.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")
    if body.role not in ("admin", "member", "reviewer"):
        raise HTTPException(status_code=400, detail="Invalid role")

    from draftly.integrations.clerk.client import update_member_role

    result = await update_member_role(org_id, body.user_id, body.role)
    return result


# ── Reviewer self-registration ────────────────────────────────


@router.post("/self")
async def register_as_reviewer(
    request: Request,
    body: SelfRegisterRequest,
    token: dict = Depends(require_reviewer_role),
) -> dict[str, Any]:
    """Register the current user as a reviewer for their org."""
    org_id = token.get("org_id")
    clerk_user_id = token.get("user_id")
    if not org_id or not clerk_user_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    repo = _get_repo(request)

    existing = await repo.get_reviewer_by_clerk_user(org_id, clerk_user_id)
    if existing:
        raise HTTPException(status_code=409, detail="Already registered as reviewer")

    # Look up user info from Clerk API
    from draftly.integrations.clerk.client import get_user

    try:
        user = await get_user(clerk_user_id)
    except Exception:
        raise HTTPException(status_code=404, detail="User not found")

    name = user.get("first_name", "")
    if user.get("last_name"):
        name = f"{name} {user['last_name']}".strip()
    if not name:
        name = user.get("username", "Unknown")

    email_addresses = user.get("email_addresses", [])
    email = email_addresses[0].get("email_address") if email_addresses else None

    reviewer = await repo.create_reviewer(
        org_id=org_id,
        name=name,
        email=email,
        clerk_user_id=clerk_user_id,
        slack_user_id=body.slack_user_id,
        discord_user_id=body.discord_user_id,
        notify_slack=body.notify_slack,
        notify_discord=body.notify_discord,
        notify_email=body.notify_email,
    )
    return ReviewersRepository.record_to_dict(reviewer)


# ── Standard CRUD ─────────────────────────────────────────────


@router.post("")
async def create(
    request: Request,
    body: CreateReviewerRequest,
    token: dict = Depends(require_admin_role),
) -> dict[str, Any]:
    """Create a new reviewer (admin only)."""
    org_id = token.get("org_id") or body.org_id
    if not org_id:
        raise HTTPException(status_code=400, detail="No organization selected")

    repo = _get_repo(request)
    reviewer = await repo.create_reviewer(
        org_id=org_id,
        name=body.name,
        email=body.email,
        slack_user_id=body.slack_user_id,
        discord_user_id=body.discord_user_id,
        notify_slack=body.notify_slack,
        notify_discord=body.notify_discord,
        notify_email=body.notify_email,
    )
    return ReviewersRepository.record_to_dict(reviewer)


@router.get("")
async def list_reviewers(
    request: Request,
    token: dict = Depends(get_verified_token),
    org_id: str | None = None,
    active_only: bool = True,
) -> dict[str, Any]:
    """List reviewers for the current organization."""
    effective_org = org_id or token.get("org_id")
    if not effective_org:
        return {"reviewers": []}

    repo = _get_repo(request)
    reviewers = await repo.get_reviewers_by_org(effective_org, active_only=active_only)
    return {"reviewers": [ReviewersRepository.record_to_dict(r) for r in reviewers]}


@router.get("/{reviewer_id}")
async def get_reviewer(
    request: Request,
    reviewer_id: str,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Get a reviewer by ID."""
    repo = _get_repo(request)
    reviewer = await repo.get_reviewer_by_id(reviewer_id)
    if not reviewer:
        raise HTTPException(status_code=404, detail="Reviewer not found")
    return ReviewersRepository.record_to_dict(reviewer)


@router.put("/{reviewer_id}")
async def update(
    request: Request,
    reviewer_id: str,
    body: UpdateReviewerRequest,
    token: dict = Depends(get_verified_token),
) -> dict[str, Any]:
    """Update a reviewer (admin: any reviewer; reviewer: own profile only)."""
    repo = _get_repo(request)

    existing = await repo.get_reviewer_by_id(reviewer_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Reviewer not found")

    if existing.org_id != token.get("org_id"):
        raise HTTPException(status_code=403, detail="Reviewer not found in your organization")

    is_admin = token.get("org_role") == "admin"
    is_self = existing.clerk_user_id == token.get("user_id")

    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail="Can only edit your own profile")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    if not is_admin:
        allowed_for_reviewer = {
            "slack_user_id",
            "discord_user_id",
            "notify_slack",
            "notify_discord",
            "notify_email",
        }
        updates = {k: v for k, v in updates.items() if k in allowed_for_reviewer}
        if not updates:
            raise HTTPException(
                status_code=400,
                detail="Reviewers can only update notification preferences and platform IDs",
            )

    updated = await repo.update_reviewer(reviewer_id, **updates)
    return ReviewersRepository.record_to_dict(updated)


@router.delete("/{reviewer_id}")
async def delete(
    request: Request,
    reviewer_id: str,
    token: dict = Depends(require_admin_role),
) -> dict[str, str]:
    """Delete a reviewer (admin only)."""
    repo = _get_repo(request)

    existing = await repo.get_reviewer_by_id(reviewer_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Reviewer not found")

    if existing.org_id != token.get("org_id"):
        raise HTTPException(status_code=403, detail="Reviewer not found in your organization")

    await repo.delete_reviewer(reviewer_id)
    return {"status": "deleted"}
