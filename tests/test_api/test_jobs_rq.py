"""Tests for RQ-based job API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestRunJobEnqueue:
    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    @patch("draftly.app.api.routes.jobs.enqueue_job")
    async def test_run_job_returns_queued(self, mock_enqueue, mock_store_cls):
        mock_job = MagicMock()
        mock_job.id = "test-job-id"
        mock_enqueue.return_value = mock_job
        mock_store_cls.return_value.insert = AsyncMock()

        from draftly.app.api.routes.jobs import run_job, JobRequest

        request = MagicMock()
        request.app.state.draftly = MagicMock()
        request.app.state.draftly.rq_queues = {"default": MagicMock()}
        request.app.state.draftly.task_handlers = {"test.task": MagicMock()}

        body = JobRequest(job_name="test.task", arguments={"key": "val"})

        result = await run_job(body, request)

        assert result["status"] == "queued"
        assert result["job_id"] == "test-job-id"

    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    @patch("draftly.app.api.routes.jobs.enqueue_job")
    async def test_run_job_delegates_to_enqueue_job(self, mock_enqueue, mock_store_cls):
        mock_job = MagicMock()
        mock_job.id = "j2"
        mock_enqueue.return_value = mock_job
        mock_store_cls.return_value.insert = AsyncMock()

        from draftly.app.api.routes.jobs import run_job, JobRequest

        request = MagicMock()
        request.app.state.draftly = MagicMock()
        request.app.state.draftly.rq_queues = {"default": MagicMock()}
        request.app.state.draftly.task_handlers = {"documentation.sync": MagicMock()}

        body = JobRequest(job_name="documentation.sync", arguments={"repo": "x"})

        await run_job(body, request)

        mock_enqueue.assert_called_once_with(
            queues=request.app.state.draftly.rq_queues,
            task_handlers=request.app.state.draftly.task_handlers,
            task_name="documentation.sync",
            repo="x",
        )

    @pytest.mark.asyncio
    async def test_run_job_missing_rq_returns_503(self):
        from draftly.app.api.routes.jobs import run_job, JobRequest

        request = MagicMock()
        request.app.state.draftly = MagicMock()
        request.app.state.draftly.rq_queues = None
        request.app.state.draftly.task_handlers = None

        body = JobRequest(job_name="test.task")

        with pytest.raises(HTTPException) as exc_info:
            await run_job(body, request)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_run_job_unknown_task_returns_404(self):
        from draftly.app.api.routes.jobs import run_job, JobRequest

        request = MagicMock()
        request.app.state.draftly = MagicMock()
        request.app.state.draftly.rq_queues = {"default": MagicMock()}
        request.app.state.draftly.task_handlers = {"known.task": MagicMock()}

        body = JobRequest(job_name="unknown.task")

        with pytest.raises(HTTPException) as exc_info:
            await run_job(body, request)
        assert exc_info.value.status_code == 404


class TestListJobs:
    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    async def test_list_jobs_returns_items(self, mock_store_cls):
        mock_store = MagicMock()
        mock_store.list_active = AsyncMock(
            return_value=[
                {"id": "j1", "status": "active"},
                {"id": "j2", "status": "active"},
            ]
        )
        mock_store_cls.return_value = mock_store

        from draftly.app.api.routes.jobs import list_jobs

        result = await list_jobs()
        assert len(result["items"]) == 2
        assert result["items"][0]["id"] == "j1"

    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    async def test_list_jobs_calls_store(self, mock_store_cls):
        mock_store = MagicMock()
        mock_store.list_active = AsyncMock(return_value=[])
        mock_store_cls.return_value = mock_store

        from draftly.app.api.routes.jobs import list_jobs

        result = await list_jobs()
        mock_store.list_active.assert_called_once()
        assert result["items"] == []


class TestGetJob:
    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    async def test_get_job_returns_detail(self, mock_store_cls):
        mock_store = MagicMock()
        mock_store.get = AsyncMock(
            return_value={
                "id": "j1",
                "status": "active",
                "job_type": "documentation.sync",
            }
        )
        mock_store_cls.return_value = mock_store

        from draftly.app.api.routes.jobs import get_job

        result = await get_job("j1")
        assert result["id"] == "j1"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    async def test_get_job_not_found_returns_404(self, mock_store_cls):
        mock_store = MagicMock()
        mock_store.get = AsyncMock(return_value=None)
        mock_store_cls.return_value = mock_store

        from draftly.app.api.routes.jobs import get_job

        with pytest.raises(HTTPException) as exc_info:
            await get_job("nonexistent")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("draftly.app.api.routes.jobs.DatabaseJobsStore")
    async def test_get_job_passes_id_to_store(self, mock_store_cls):
        mock_store = MagicMock()
        mock_store.get = AsyncMock(
            return_value={"id": "abc", "status": "pending"}
        )
        mock_store_cls.return_value = mock_store

        from draftly.app.api.routes.jobs import get_job

        await get_job("abc")
        mock_store.get.assert_called_once_with(job_id="abc")
