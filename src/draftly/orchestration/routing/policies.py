"""Review policy resolution shared by the ReviewGate hook and runners.

Policies:
- ``"always"``: every delivery requires human approval.
- ``"risky"``: only high-risk deliveries require approval (breaking
  changes, deprecations, API changes, or high urgency).
- ``"never"``: auto-approve everything (not recommended for production).
"""

from __future__ import annotations

RISKY_CHANGE_TYPES = frozenset({"breaking_change", "deprecation", "api_change"})

VALID_POLICIES = ("always", "risky", "never")


def resolve_review_policy(raw: str | None) -> str:
    """Normalize an untrusted policy string; unknown values mean 'always'."""
    if raw in VALID_POLICIES:
        return raw
    return "always"


def is_risky(classification: dict | None) -> bool:
    """Decide whether a classification marks a run as risky."""
    if not classification:
        return True
    change_type = str(classification.get("change_type", ""))
    urgency = str(classification.get("urgency", ""))
    return change_type in RISKY_CHANGE_TYPES or urgency == "high"


def should_review(policy: str, classification: dict | None = None) -> bool:
    """Gate decision for the delivery node under the given policy."""
    policy = resolve_review_policy(policy)
    if policy == "never":
        return False
    if policy == "always":
        return True
    return is_risky(classification)
