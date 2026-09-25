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


def _tree_entry_matches_prefix(path: str, prefix: str) -> bool:
    """True when a tree entry lives at or under ``prefix`` (no leading slash)."""
    return path == prefix or path.startswith(prefix + "/")


def _filter_tree_entries(
    entries: list[dict[str, Any]], path_prefix: str | None
) -> list[dict[str, Any]]:
    """Keep only the entries inside ``path_prefix`` (all entries when unset)."""
    prefix = (path_prefix or "").strip("/")
    if not prefix:
        return list(entries)
    return [
        entry
        for entry in entries
        if _tree_entry_matches_prefix(str(entry.get("path", "")), prefix)
    ]


def _subtree_can_contribute(path: str, path_prefix: str | None) -> bool:
    """True when walking ``path``'s subtree can still yield ``path_prefix`` hits."""
    prefix = (path_prefix or "").strip("/")
    if not prefix:
        return True
    root = prefix.split("/")[0]
    return bool(root) and _tree_entry_matches_prefix(path, root)


def _entry_budget_spent(
    entries: list[dict[str, Any]], path_prefix: str | None, max_entries: int | None
) -> bool:
    """True when the walk already holds ``max_entries`` matching entries.

    Every returned entry (directory or file) counts: the budget exists to keep
    one tool result inside the model's per-turn output budget, so it must bound
    the whole payload, not just the files.
    """
    if max_entries is None:
        return False
    return len(_filter_tree_entries(entries, path_prefix)) >= max_entries


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
        """Get recursive Git tree for a repository ref."""
        page = await self.get_tree_bounded(owner, repo, ref, token)
        return page["entries"]

    async def get_tree_bounded(
        self,
        owner: str,
        repo: str,
        ref: str,
        token: str | None = None,
        *,
        path_prefix: str | None = None,
        max_entries: int | None = None,
    ) -> dict[str, Any]:
        """Recursive tree listing bounded by ``max_entries``, with metadata.

        A truncated recursive response cannot be paginated, so this completes it
        by walking the remaining subtrees — but the walk stops as soon as the
        caller's entry budget is spent, directories that cannot contribute to
        ``path_prefix`` are never fetched, and the result reports ``truncated``
        so callers never mistake a partial listing for a complete one.

        An un-bounded listing is what starved the PR writer in run e1e96f90:
        each ``github_get_tree`` result exceeded the model's per-response budget,
        Strands replaced it with a max-tokens error, and the writer retried the
        same call — 18 times — before running out of turns.
        """
        data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
            token=token,
        )

        entries: list[dict[str, Any]] = list(data.get("tree", []))
        walk_cut_short = False
        if data.get("truncated", False):
            logger.warning(
                "github_tree_truncated owner=%s repo=%s ref=%s", owner, repo, ref
            )
            pending = [
                entry
                for entry in entries
                if entry.get("type") == "tree"
                and _subtree_can_contribute(str(entry.get("path", "")), path_prefix)
            ]
            while pending:
                if _entry_budget_spent(entries, path_prefix, max_entries):
                    walk_cut_short = True
                    break
                entry = pending.pop(0)
                subtree = await self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/git/trees/{entry['sha']}",
                    params={"recursive": "1"},
                    token=token,
                )
                children: list[dict[str, Any]] = list(subtree.get("tree", []))
                entries.extend(children)
                pending.extend(
                    child
                    for child in children
                    if child.get("type") == "tree"
                    and _subtree_can_contribute(
                        str(child.get("path", "")), path_prefix
                    )
                )

        selected = _filter_tree_entries(entries, path_prefix)
        if max_entries is not None and len(selected) > max_entries:
            return {"entries": selected[:max_entries], "truncated": True}
        return {"entries": selected, "truncated": walk_cut_short}

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
