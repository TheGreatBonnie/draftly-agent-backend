"""PII/secrets redaction (plan §8.8) — scrub prompts before agents see them."""

from __future__ import annotations

import re

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s().-]{8,}\d")
BEARER_PATTERN = re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret)\b[:=]?\s*\S+")
AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


class RedactionService:
    """Replace sensitive spans with typed placeholders."""

    def __init__(self, *, mask_char: str = "•") -> None:
        self.mask = mask_char

    def _mask(self, value: str) -> str:
        return self.mask * min(len(value), 12)

    def redact(self, text: str) -> str:
        text = PRIVATE_KEY_PATTERN.sub("[REDACTED_PRIVATE_KEY]", text)
        text = AWS_KEY_PATTERN.sub("[REDACTED_AWS_KEY]", text)
        text = BEARER_PATTERN.sub(lambda m: f"{m.group(1)}={self._mask(m.group(0))}", text)
        text = EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
        text = PHONE_PATTERN.sub("[REDACTED_PHONE]", text)
        return text

    def contains_secret(self, text: str) -> bool:
        """True when the text looks like it embeds a credential."""
        return bool(
            AWS_KEY_PATTERN.search(text)
            or PRIVATE_KEY_PATTERN.search(text)
            or BEARER_PATTERN.search(text)
        )
