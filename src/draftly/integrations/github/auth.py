from __future__ import annotations

import os


class GitHubAuth:
    """
    GitHub authentication configuration.

    Authentication details remain inside the integration layer.
    """

    def __init__(
        self,
        token: str | None = None,
    ):
        self.token = token or os.getenv("GITHUB_TOKEN")

        if not self.token:
            raise RuntimeError(
                "GITHUB_TOKEN is not configured."
            )

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
