from __future__ import annotations

from typing import Any, cast

import httpx

from .app_auth import (
    add_issue_labels,
    generate_jwt,
    get_installation_info,
    get_installation_repositories,
    get_installation_token,
    post_issue_comment,
)
from .auth import GitHubAuth


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
    ):
        self.auth = auth or GitHubAuth()
        self.timeout = timeout
        self.repository = repository

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.auth.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def create_pull_request(
        self,
        owner: str,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> dict:

        url = (
            f"{self.BASE_URL}/repos/"
            f"{owner}/{repository}/pulls"
        )

        response = httpx.post(
            url,
            headers=self._headers(),
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            },
            timeout=30,
        )

        response.raise_for_status()

        return cast(dict, response.json())

    def get_pull_request(
        self,
        owner: str,
        repository: str,
        number: int,
    ) -> dict:

        url = (
            f"{self.BASE_URL}/repos/"
            f"{owner}/{repository}/pulls/{number}"
        )

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
    ) -> Any:
        url = f"{self.BASE_URL}{path}"

        async with httpx.AsyncClient(
            timeout=self.timeout,
        ) as client:
            response = await client.request(
                method,
                url,
                headers=self.auth.headers(),
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
    ) -> str:
        url = f"{self.BASE_URL}{path}"

        headers = self.auth.headers()
        if accept:
            headers = {**headers, "Accept": accept}

        async with httpx.AsyncClient(
            timeout=self.timeout,
        ) as client:
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
        return cast(list[dict[str, Any]], await self._request(
            "GET",
            f"/repos/{repository}/pulls/{pull_request_number}/files",
        ))

    async def create_ref(
        self,
        repository: str,
        name: str,
        sha: str,
    ) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request(
            "POST",
            f"/repos/{repository}/git/refs",
            json={
                "ref": f"refs/heads/{name}",
                "sha": sha,
            },
        ))

    async def create_commit_and_tree(
        self,
        repository: str,
        branch: str,
        message: str,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        branch_ref = cast(dict[str, Any], await self._request(
            "GET",
            f"/repos/{repository}/git/ref/heads/{branch}",
        ))
        branch_sha = cast(str, branch_ref["object"]["sha"])

        tree = cast(dict[str, Any], await self._request(
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
        ))
        tree_sha = cast(str, tree["sha"])

        commit = cast(dict[str, Any], await self._request(
            "POST",
            f"/repos/{repository}/git/commits",
            json={
                "message": message,
                "tree": tree_sha,
                "parents": [branch_sha],
            },
        ))

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
        return cast(dict[str, Any], await self._request(
            "POST",
            f"/repos/{repository}/issues/{pull_request_number}/comments",
            json={"body": body},
        ))

    async def get_issue(
        self,
        repository: str,
        issue_number: int,
    ) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request(
            "GET",
            f"/repos/{repository}/issues/{issue_number}",
        ))

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
        return cast(dict[str, Any], await self._request(
            "GET",
            f"/repos/{repository}/pulls/{pull_request_number}",
        ))

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
                "q": (
                    f"repo:{repository} "
                    f"is:pr {query}"
                ),
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
            path = (
                f"/repos/{repository}/releases/"
                f"{release_identifier}"
            )
        else:
            path = (
                f"/repos/{repository}/releases/tags/"
                f"{release_identifier}"
            )

        return cast(dict[str, Any], await self._request(
            "GET",
            path,
        ))

    async def list_releases(
        self,
        repository: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], await self._request(
            "GET",
            f"/repos/{repository}/releases",
            params={
                "per_page": limit,
            },
        ))

    async def get_repository(
        self,
        repository: str,
    ) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request(
            "GET",
            f"/repos/{repository}",
        ))

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
