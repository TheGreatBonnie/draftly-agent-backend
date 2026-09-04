"""Unit tests for repository tools (filesystem, git, code search)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from draftly.tools._guard import EmptyToolInputError
from draftly.tools.repository.code_search import code_search
from draftly.tools.repository.filesystem import (
    file_exists,
    list_directory,
    read_file,
    write_file,
)
from draftly.tools.repository.git import git_diff, git_log, git_status

_HAVE_GIT = shutil.which("git") is not None


@pytest.mark.asyncio
async def test_read_write_file(tmp_path) -> None:
    target = tmp_path / "nested" / "doc.md"
    result = await write_file(str(target), "hello world")
    assert result["path"] == str(target)
    assert result["bytes"] == len("hello world")
    assert await read_file(str(target)) == "hello world"


@pytest.mark.asyncio
async def test_list_directory_and_file_exists(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    entries = await list_directory(str(tmp_path))
    names = {e["name"] for e in entries}
    assert names == {"a.txt", "sub"}
    assert await file_exists(str(tmp_path / "a.txt"))
    assert not await file_exists(str(tmp_path / "missing"))


@pytest.mark.asyncio
async def test_code_search_finds_and_respects_limit(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "def connect():\n    return 'pool'\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "other.py").write_text(
        "pool = []\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("no match here\n", encoding="utf-8")

    matches = await code_search("pool", str(tmp_path))
    assert len(matches) == 2
    assert all(m["path"].startswith("src/") for m in matches)
    assert matches[0]["line"] == 2

    limited = await code_search("pool", str(tmp_path), limit=1)
    assert len(limited) == 1


@pytest.mark.asyncio
async def test_code_search_skips_hidden_dirs(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("pool", encoding="utf-8")
    (tmp_path / "app.py").write_text("pool", encoding="utf-8")
    matches = await code_search("pool", str(tmp_path))
    assert [m["path"] for m in matches] == ["app.py"]


@pytest.mark.skipif(not _HAVE_GIT, reason="git binary not available")
@pytest.mark.asyncio
async def test_git_tools(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "file.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"],
        cwd=tmp_path,
        check=True,
    )

    log = await git_log(str(tmp_path), limit=5)
    assert len(log) == 1
    assert log[0]["subject"] == "initial"

    (tmp_path / "file.txt").write_text("v2\n", encoding="utf-8")
    status = await git_status(str(tmp_path))
    assert "file.txt" in status
    diff = await git_diff(str(tmp_path), base="HEAD")
    assert "v2" in diff


class TestEmptyInputGuards:
    """A Strands tool-input parse drop yields {}; tools must reject it loudly.

    Otherwise code_search walks the current dir with an empty query, returns
    garbage, and the model thrashes the tool until the node times out.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "query",
        ["", "   ", None],
    )
    async def test_code_search_rejects_empty_query(self, query) -> None:
        with pytest.raises(EmptyToolInputError):
            await code_search(query, "/some/repo")

    @pytest.mark.asyncio
    async def test_code_search_rejects_empty_repo_dir(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await code_search("foo", "")

    @pytest.mark.asyncio
    async def test_read_file_rejects_empty_path(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await read_file("")

    @pytest.mark.asyncio
    async def test_write_file_rejects_empty_path(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await write_file("", "content")

    @pytest.mark.asyncio
    async def test_list_directory_rejects_empty_path(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await list_directory("")

    @pytest.mark.asyncio
    async def test_git_tools_reject_empty_repo_dir(self) -> None:
        with pytest.raises(EmptyToolInputError):
            await git_diff("")
        with pytest.raises(EmptyToolInputError):
            await git_status("")
        with pytest.raises(EmptyToolInputError):
            await git_log("")
