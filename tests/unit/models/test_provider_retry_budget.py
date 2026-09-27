"""Providers must not retry behind the failover layer's back.

``ProviderConfig.max_retries`` feeds the OpenAI SDK's internal retry loop,
which runs *before* ``PaymentAwareModel`` ever sees the exception. With the
SDK set to 2, a timed-out request burned 3x60s on one provider and only then
surfaced to the wrapper that could have rotated. Zeroing it makes the wrapper
the single owner of retry/failover, so a rotation happens after one failed
attempt instead of three.
"""

from __future__ import annotations

from draftly.models.config import ProviderConfig


def test_provider_does_not_retry_internally() -> None:
    """The failover layer owns retrying; the SDK must not pre-empt it."""
    assert ProviderConfig(name="p", api_key=None, base_url=None).max_retries == 0


def test_provider_timeout_still_bounds_a_single_attempt() -> None:
    """max_retries=0 removes retries, not the per-attempt deadline."""
    assert ProviderConfig(name="p", api_key=None, base_url=None).timeout == 60.0
