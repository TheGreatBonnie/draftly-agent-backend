from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, cast

import httpx
import structlog

from .app_auth import (
    add_issue_labels,
    generate_jwt,
    get_installation_info,
    get_installation_repositories,
    get_installation_token,
    post_issue_comment,
)
from .auth import GitHubAuth
from .runtime import current_installation_id

logger = structlog.get_logger(__name__)


class GitHubClient:
    """
    Low-level GitHub API client.

    This class should contain API-specific concerns only:
    authentication, HTTP requests, URLs, status handling, etc.
    """

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        auth: GitHubAuth | None = None,
        timeout: float = 30.0,
        repository: str | None = None,
        installation_id: int | None = None,
    ):
        self.installation_id = installation_id or current_installation_id()
        # Installation credentials are acquired lazily in _request because
        # token exchange is asynchronous. Standalone callers retain the
        # existing GITHUB_TOKEN behavior.
        self.auth = auth or (None if self.installation_id else GitHubAuth())
        self.timeout = timeout
        self.repository = repository
        self._shared_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._shared_client is None:
            self._shared_client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._shared_client

    async def aclose(self) -> None:
        if self._shared_client is not None:
            await self._shared_client.aclose()
            self._shared_client = None

    def _headers(self, token: str | None = None) -> dict[str, str]:
        resolved_token = token or (self.auth.token if self.auth else None)
        if not resolved_token:
            raise RuntimeError("GitHub authentication is not configured.")
        return {
            "Authorization": f"Bearer {resolved_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def create_pull_request(
        self,
        owner: str,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> dict:

        return cast(
            dict,
            await self._request(
                "POST",
                f"/repos/{owner}/{repository}/pulls",
                json={
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": base,
                },
            ),
        )

    def get_pull_request(
        self,
        owner: str,
        repository: str,
        number: int,
    ) -> dict:

        url = f"{self.BASE_URL}/repos/{owner}/{repository}/pulls/{number}"

        response = httpx.get(
            url,
            headers=self._headers(),
            timeout=30,
        )

        response.raise_for_status()

        return cast(dict[Any, Any], response.json())

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> Any:
        url = f"{self.BASE_URL}{path}"
        resolved_token = token
        if resolved_token is None and self.installation_id is not None:
            resolved_token = await get_installation_token(self.installation_id)
        headers = self._headers(resolved_token)

        client = self._client()
        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
        )

        response.raise_for_status()

        return response.json()

    async def _request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        accept: str | None = None,
        token: str | None = None,
    ) -> str:
        url = f"{self.BASE_URL}{path}"

        resolved_token = token
        if resolved_token is None and self.installation_id is not None:
            resolved_token = await get_installation_token(self.installation_id)
        headers = self._headers(resolved_token)
        if accept:
            headers = {**headers, "Accept": accept}

        client = self._client()
        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
        )

        response.raise_for_status()

        return response.text

    async def get_pull_request_diff(
        self,
        repository: str,
        pull_request_number: int,
    ) -> str:
        return await self._request_text(
            "GET",
            f"/repos/{repository}/pulls/{pull_request_number}",
            accept="application/vnd.github.v3.diff",
        )

    async def get_pull_request_files(
        self,
        repository: str,
        pull_request_number: int,
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET",
                f"/repos/{repository}/pulls/{pull_request_number}/files",
            ),
        )

    async def create_ref(
        self,
        repository: str,
        name: str,
        sha: str,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/repos/{repository}/git/refs",
                json={
                    "ref": f"refs/heads/{name}",
                    "sha": sha,
                },
            ),
        )

    async def get_branch_head_sha(
        self,
        repository: str,
        branch: str | None = None,
    ) -> str:
        """Resolve the HEAD SHA of a branch (defaults to the repo default)."""
        if not branch:
            repo_ref = await self.get_repository(repository)
            branch = repo_ref.get("default_branch") or "main"
        ref = cast(
            dict[str, Any],
            await self._request(
                "GET",
                f"/repos/{repository}/git/ref/heads/{branch}",
            ),
        )
        return ref["object"]["sha"]

    async def create_commit_and_tree(
        self,
        repository: str,
        branch: str,
        message: str,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        branch_ref = cast(
            dict[str, Any],
            await self._request(
                "GET",
                f"/repos/{repository}/git/ref/heads/{branch}",
            ),
        )
        branch_sha = cast(str, branch_ref["object"]["sha"])

        tree = cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/repos/{repository}/git/trees",
                json={
                    "base_tree": branch_sha,
                    "tree": [
                        {
                            "path": file["path"],
                            "mode": file.get("mode", "100644"),
                            "type": "blob",
                            "content": file["content"],
                        }
                        for file in files
                    ],
                },
            ),
        )
        tree_sha = cast(str, tree["sha"])

        commit = cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/repos/{repository}/git/commits",
                json={
                    "message": message,
                    "tree": tree_sha,
                    "parents": [branch_sha],
                },
            ),
        )

        await self._request(
            "PATCH",
            f"/repos/{repository}/git/refs/heads/{branch}",
            json={"sha": commit["sha"]},
        )

        return commit

    async def create_comment(
        self,
        repository: str,
        pull_request_number: int,
        body: str,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                f"/repos/{repository}/issues/{pull_request_number}/comments",
                json={"body": body},
            ),
        )

    async def get_issue(
        self,
        repository: str,
        issue_number: int,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "GET",
                f"/repos/{repository}/issues/{issue_number}",
            ),
        )

    async def search_issues(
        self,
        repository: str,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/search/issues",
            params={
                "q": f"repo:{repository} {query}",
                "per_page": limit,
            },
        )

        return cast(list[dict[str, Any]], data.get("items", []))

    async def get_pull_request_async(  # noqa: F811
        self,
        repository: str,
        pull_request_number: int,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "GET",
                f"/repos/{repository}/pulls/{pull_request_number}",
            ),
        )

    async def search_pull_requests(
        self,
        repository: str,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/search/issues",
            params={
                "q": (f"repo:{repository} is:pr {query}"),
                "per_page": limit,
            },
        )

        return cast(list[dict[str, Any]], data.get("items", []))

    async def get_release(
        self,
        repository: str,
        release_identifier: str,
    ) -> dict[str, Any]:
        if release_identifier.isdigit():
            path = f"/repos/{repository}/releases/{release_identifier}"
        else:
            path = f"/repos/{repository}/releases/tags/{release_identifier}"

        return cast(
            dict[str, Any],
            await self._request(
                "GET",
                path,
            ),
        )

    async def list_releases(
        self,
        repository: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET",
                f"/repos/{repository}/releases",
                params={
                    "per_page": limit,
                },
            ),
        )

    async def get_tree(
        self,
        owner: str,
        repo: str,
        ref: str,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recursive Git tree for a repository ref.

        A truncated recursive response cannot be paginated, so fall back to
        walking each root directory's subtree by sha.
        """
        data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
            token=token,
        )

        if not data.get("truncated", False):
            return list(data.get("tree", []))

        logger.warning(
            "github_tree_truncated owner=%s repo=%s ref=%s", owner, repo, ref
        )
        all_entries: list[dict[str, Any]] = []
        pending = [
            entry for entry in data.get("tree", []) if entry.get("type") == "tree"
        ]
        while pending:
            entry = pending.pop(0)
            subtree = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/git/trees/{entry['sha']}",
                params={"recursive": "1"},
                token=token,
            )
            all_entries.extend(subtree.get("tree", []))
        return all_entries

    async def get_file_contents(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
        token: str | None = None,
    ) -> str:
        """Get decoded file contents from a repository."""
        data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            token=token,
        )

        # Skip files > 1MB
        if data.get("size", 0) > 1_000_000:
            logger.warning("file_too_large path=%s size=%d", path, data.get("size", 0))
            return ""

        content = data.get("content", "")
        encoding = data.get("encoding", "")

        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return content

    async def get_last_commit_date(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
        token: str,
    ) -> datetime | None:
        """Return the commit date of the most recent commit touching `path`, or None."""
        try:
            data = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/commits",
                params={"path": path, "sha": ref, "per_page": 1},
                token=token,
            )
            if not data:
                return None
            date_str = data[0]["commit"]["committer"]["date"]
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            logger.warning(
                "get_last_commit_date_failed owner=%s repo=%s path=%s",
                owner,
                repo,
                path,
            )
            return None

    async def get_repository(
        self,
        repository: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "GET",
                f"/repos/{repository}",
                token=token,
            ),
        )

    async def search_code(
        self,
        repository: str,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            "/search/code",
            params={
                "q": f"repo:{repository} {query}",
                "per_page": limit,
            },
        )

        return cast(list[dict[str, Any]], data.get("items", []))

    async def generate_jwt(self) -> str:
        return generate_jwt()

    async def get_installation_token(self, installation_id: int) -> str:
        return await get_installation_token(installation_id)

    async def get_installation_info(self, installation_id: int) -> dict:
        return cast(dict, await get_installation_info(installation_id))

    async def get_installation_repositories(self, token: str) -> list[dict]:
        return cast(list[dict], await get_installation_repositories(token))

    async def post_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str, token: str
    ) -> dict:
        return cast(dict, await post_issue_comment(owner, repo, issue_number, body, token))

    async def add_issue_labels(
        self, owner: str, repo: str, issue_number: int, labels: list[str], token: str
    ) -> dict:
        return cast(dict, await add_issue_labels(owner, repo, issue_number, labels, token))
        return await add_issue_labels(owner, repo, issue_number, labels, token)
