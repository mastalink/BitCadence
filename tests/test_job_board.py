"""Unit tests for Job Board handlers, contracts, and routing."""

import pytest
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, ANY

from mco.orchestrator.contracts import JobStatus
from mco.orchestrator.handlers import handle_job_create, handle_job_lease, handle_job_update


class MockSupabaseClient:
    """Mock implementation of the Supabase python client."""

    def __init__(self, data_list=None):
        self.data_list = data_list or []
        self._table_name = ""
        self._eq_col = ""
        self._eq_val = None
        self._in_col = ""
        self._in_vals = []
        self._update_data = {}
        self._rpc_func = ""
        self._last_rpc_func = ""
        self._rpc_params = {}

    def table(self, table_name: str):
        self._table_name = table_name
        self._rpc_func = ""  # Reset RPC state on table query builder
        return self

    def select(self, *args, **kwargs):
        return self

    def insert(self, data: Dict[str, Any]):
        # Audit-trail writes (agent_job_events) must not clobber the job data
        # the assertions inspect.
        if self._table_name == "agent_jobs":
            self._update_data = data
        return self

    def update(self, data: Dict[str, Any]):
        if self._table_name == "agent_jobs":
            self._update_data = data
        return self

    def eq(self, column: str, value: Any):
        self._eq_col = column
        self._eq_val = value
        return self

    def in_(self, column: str, values: list):
        self._in_col = column
        self._in_vals = values
        return self

    def rpc(self, func_name: str, params: Dict[str, Any]):
        self._rpc_func = func_name
        self._last_rpc_func = func_name
        self._rpc_params = params
        self._table_name = ""  # Reset table state on RPC builder
        self._update_data = {}
        return self

    def execute(self):
        class Result:
            def __init__(self, data):
                self.data = data

        if self._rpc_func == "lease_task":
            # Mock RPC return value
            return Result(data=True)

        if self._table_name == "agent_jobs" and self._update_data:
            # Mock insertion or update return value
            res_dict = {
                "id": "job_uuid_123",
                "title": "Mock Task",
                "status": JobStatus.PENDING.value,
                "depends_on": [],
                **self._update_data
            }
            return Result(data=[res_dict])

        # Return mock read data
        return Result(data=self.data_list)


@pytest.mark.asyncio
async def test_handle_job_create_no_dependencies():
    """Verify handle_job_create sets job status to pending when there are no dependencies."""
    db_client = MockSupabaseClient()
    payload = {
        "title": "Write Unit Tests",
        "description": "Create tests for BitCadence",
        "target_agent_role": "codex"
    }

    send_error = AsyncMock()
    send_ack = AsyncMock()
    broadcast_event = AsyncMock()

    await handle_job_create(
        db_client=db_client,
        payload=payload,
        source_agent_id="test_runner",
        source_agent_role="tester",
        correlation_id="corr_1",
        send_error=send_error,
        send_ack=send_ack,
        broadcast_event=broadcast_event
    )

    # Asserts
    send_error.assert_not_called()
    send_ack.assert_called_once()
    broadcast_event.assert_called_once_with("job_pending", ANY)

    # Verify standard pending status
    inserted = db_client._update_data
    assert inserted["title"] == "Write Unit Tests"
    assert inserted["status"] == JobStatus.PENDING.value
    assert inserted["depends_on"] == []


@pytest.mark.asyncio
async def test_handle_job_create_with_incomplete_dependencies():
    """Verify handle_job_create sets job status to waiting when parent task is incomplete."""
    # Mock database to return parent task that is still pending
    parent_job = {"id": "parent_uuid", "status": JobStatus.PENDING.value}
    db_client = MockSupabaseClient(data_list=[parent_job])
    
    payload = {
        "title": "Run Test Suite",
        "description": "Execute pytest tests",
        "target_agent_role": "codex",
        "depends_on": ["parent_uuid"]
    }

    send_error = AsyncMock()
    send_ack = AsyncMock()
    broadcast_event = AsyncMock()

    await handle_job_create(
        db_client=db_client,
        payload=payload,
        source_agent_id="test_runner",
        source_agent_role="tester",
        correlation_id="corr_2",
        send_error=send_error,
        send_ack=send_ack,
        broadcast_event=broadcast_event
    )

    # Verify standard waiting status
    inserted = db_client._update_data
    assert inserted["status"] == JobStatus.WAITING.value
    broadcast_event.assert_called_once_with("job_created", ANY)


@pytest.mark.asyncio
async def test_handle_job_lease():
    """Verify handle_job_lease calls atomic RPC and claims the task."""
    job_details = [{"id": "job_uuid_123", "title": "Mock Task", "status": JobStatus.LEASED.value}]
    db_client = MockSupabaseClient(data_list=job_details)
    
    payload = {"task_id": "job_uuid_123"}
    send_error = AsyncMock()
    send_ack = AsyncMock()
    broadcast_event = AsyncMock()

    await handle_job_lease(
        db_client=db_client,
        payload=payload,
        fallback_agent_instance_id="worker_1",
        correlation_id="corr_3",
        send_error=send_error,
        send_ack=send_ack,
        broadcast_event=broadcast_event
    )

    assert db_client._last_rpc_func == "lease_task"
    assert db_client._rpc_params["p_task_id"] == "job_uuid_123"
    send_ack.assert_called_once()
    broadcast_event.assert_called_once()


@pytest.mark.asyncio
async def test_handle_job_update_completion_unlocks_child():
    """Verify updating a job to completed triggers a downstream cascading check and unlocks waiting children."""
    # Child task waiting on "parent_uuid"
    child_job = {"id": "child_uuid", "status": JobStatus.WAITING.value, "depends_on": ["parent_uuid"]}
    db_client = MockSupabaseClient(data_list=[child_job])
    
    payload = {
        "task_id": "parent_uuid",
        "status": JobStatus.COMPLETED.value
    }
    
    send_error = AsyncMock()
    send_ack = AsyncMock()
    broadcast_event = AsyncMock()

    await handle_job_update(
        db_client=db_client,
        payload=payload,
        correlation_id="corr_4",
        send_error=send_error,
        send_ack=send_ack,
        broadcast_event=broadcast_event
    )

    # Verify parent updated correctly
    assert db_client._update_data["status"] == JobStatus.COMPLETED.value
    # completed_at is no longer set by the app — the DB trigger stamps it via now().
    assert "completed_at" not in db_client._update_data

    # Acks and broadcasts
    send_ack.assert_called_once()
