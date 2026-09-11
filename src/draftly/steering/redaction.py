"""Bounded, recursive redaction for steering audit, events, and judge context.

Redaction guarantees two properties:

* Secret-shaped keys (tokens, passwords, credentials, API keys, ...) are
  replaced with ``[REDACTED]`` recursively on any nesting depth.
* The final JSON encoding of the redacted value fits within ``max_bytes``;
  oversized string values are truncated to the exact budget using the
  structural overhead of the surrounding value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"

#: Key-name fragments treated as credentials. Fragments are matched on a
#: normalized (lower-case, separator-stripped) key so ``api_key``,
#: ``api-key`` and ``ApiKey`` all collide safely.
_SECRET_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "authorization",
    "credential",
    "accesskey",
    "privatekey",
    "clientsecret",
    "signingsecret",
)


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "").replace("_", "")
    return any(marker in normalized for marker in _SECRET_MARKERS)


def _redact_secrets(value: Any) -> Any:
    """Return a deep copy with secret-shaped keys replaced by a marker."""
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_secret_key(key):
                out[key] = _REDACTED
            else:
                out[key] = _redact_secrets(item)
        return out
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True).encode("utf-8"))


def _longest_string_path(
    value: Any,
    path: tuple[Any, ...] = (),
) -> tuple[tuple[Any, ...] | None, int]:
    """Locate the (deepest path, byte length) of the largest string."""
    best_path: tuple[Any, ...] | None = None
    best_len = -1
    if isinstance(value, Mapping):
        for key, item in value.items():
            sub_path, sub_len = _longest_string_path(item, path + (key,))
            if sub_len != -1 and sub_len > best_len:
                best_path, best_len = sub_path, sub_len
    elif isinstance(value, list):
        for index, item in enumerate(value):
            sub_path, sub_len = _longest_string_path(item, path + (index,))
            if sub_len != -1 and sub_len > best_len:
                best_path, best_len = sub_path, sub_len
    elif isinstance(value, str):
        size = len(value.encode("utf-8"))
        if size > best_len:
            best_path, best_len = path, size
    if best_len == -1:
        return None, -1
    return best_path, best_len


def _get_nested(value: Any, path: tuple[Any, ...]) -> Any:
    node: Any = value
    for part in path:
        node = node[part]
    return node


def _set_nested(value: Any, path: tuple[Any, ...], replacement: Any) -> None:
    target = value
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement


def _redact_value(value: Any, *, budget: int) -> Any:
    """Shrink ``value`` in place until its encoding fits ``budget``."""
    if _json_bytes(value) <= budget:
        return value

    path, longest_len = _longest_string_path(value)
    if path is not None:
        longest = _get_nested(value, path)
        overhead = _json_bytes(value) - len(longest.encode("utf-8"))
        target = budget - overhead
        if target >= 1:
            _set_nested(value, path, longest[:target])
            if _json_bytes(value) <= budget:
                return value

    # No shrinkable string remains; deterministically prune container tails.
    if isinstance(value, dict) and value:
        value.pop(next(reversed(value)))
        return _redact_value(value, budget=budget)
    if isinstance(value, list) and value:
        value.pop()
        return _redact_value(value, budget=budget)
    return _TRUNCATED


def redact_value(value: Any, *, max_bytes: int = 4 * 1024) -> Any:
    """Return a redacted, byte-bounded copy of ``value``.

    Secret-shaped keys become ``[REDACTED]`` and the JSON encoding of the
    result never exceeds ``max_bytes``.
    """
    if max_bytes < 1:
        raise ValueError("max_bytes must be at least 1")
    redacted = _redact_secrets(value)
    return _redact_value(redacted, budget=max_bytes)