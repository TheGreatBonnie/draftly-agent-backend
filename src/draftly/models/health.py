import logging
import time
from dataclasses import dataclass, field

__all__ = [
    "FAILURE_AUTH",
    "FAILURE_CONTEXT_LENGTH",
    "FAILURE_INVALID_REQUEST",
    "FAILURE_RATE_LIMIT",
    "FAILURE_TIMEOUT",
    "FAILURE_UNAVAILABLE",
    "FALLBACK_FAILURES",
    "ProviderHealth",
    "ProviderHealthRegistry",
]

logger = logging.getLogger(__name__)

# Failure classification (doc §19).
FAILURE_TIMEOUT = "timeout"
FAILURE_RATE_LIMIT = "rate_limit"
FAILURE_UNAVAILABLE = "service_unavailable"
FAILURE_AUTH = "authentication"
FAILURE_CONTEXT_LENGTH = "context_length"
FAILURE_INVALID_REQUEST = "invalid_request"

# Failure types that should trigger provider fallback (vs retry/fail-fast).
FALLBACK_FAILURES = frozenset({FAILURE_RATE_LIMIT, FAILURE_UNAVAILABLE})


@dataclass
class ProviderHealth:
    provider: str

    healthy: bool = True

    failures: int = 0

    failure_counts: dict[str, int] = field(default_factory=dict)

    last_failure: float | None = None

    cooldown_seconds: float = 30.0

    disabled: bool = False

    # When cumulative failures reach this threshold the provider is
    # auto-disabled (until explicitly reset via ``reset()``).
    failure_threshold: int = 5

    def record_success(self) -> None:
        self.healthy = True
        self.failures = 0
        self.failure_counts.clear()

    def record_failure(
        self,
        failure_type: str = FAILURE_TIMEOUT,
    ) -> None:
        self.healthy = False
        self.failures += 1
        self.failure_counts[failure_type] = self.failure_counts.get(failure_type, 0) + 1
        self.last_failure = time.time()

        if self.failures >= self.failure_threshold:
            self.disable()

    def disable(self) -> None:
        self.disabled = True
        self.healthy = False

        logger.warning(
            "provider auto-disabled provider=%s failures=%d",
            self.provider,
            self.failures,
        )

    def reset(self) -> None:
        """Manually re-enable a provider and clear all failure state."""
        self.disabled = False
        self.healthy = True
        self.failures = 0
        self.failure_counts.clear()
        self.last_failure = None

        logger.info("provider health reset provider=%s", self.provider)

    def available(self) -> bool:
        if self.disabled:
            return False

        if self.healthy:
            return True

        if self.last_failure is None:
            return True

        elapsed = time.time() - self.last_failure

        if elapsed >= self.cooldown_seconds:
            self.healthy = True
            return True

        return False


class ProviderHealthRegistry:
    """
    Process-local health registry.

    Concurrency: mutations are simple attribute writes guarded by the
    GIL; no locking is added. If this ever moves to shared state across
    processes, serialize ``ProviderHealth`` records.
    """

    def __init__(self) -> None:
        self._health: dict[str, ProviderHealth] = {}

    def get(
        self,
        provider: str,
    ) -> ProviderHealth:
        if provider not in self._health:
            self._health[provider] = ProviderHealth(
                provider=provider,
            )

        return self._health[provider]
