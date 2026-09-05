"""Tests for the immutable audit trail module (src/mco/orchestrator/audit.py)."""

from unittest.mock import MagicMock

import pytest

from mco.orchestrator.audit import EVENTS_TABLE, get_events, record_event


# ── Helpers ────────────────────────────────────────────────────────────

def _make_mock_db(return_data=None, fail_on_insert=False):
    """Create a mock db_client that responds like a Supabase client.

    Supports both success and failure paths for record_event and
    get_events.
    """
    mock = MagicMock()
    table_mock = MagicMock()
    if fail_on_insert:
        table_mock.insert.side_effect = Exception("DB unavailable")
    else:
        table_mock.insert.return_value.execute.return_value.data = [{"id": 1}]

    # get_events flow: .table().select().eq().order().execute()
    select_chain = MagicMock()
    select_chain.eq.return_value.order.return_value.execute.return_value.data = (
        return_data or []
    )
    table_mock.select.return_value = select_chain

    mock.table.return_value = table_mock
    return mock


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    return _make_mock_db()


@pytest.fixture
def mock_db_with_events():
    events = [
        {"id": 1, "job_id": "job-1", "event": "created",   "created_at": "2026-06-01T00:00:00Z"},
        {"id": 2, "job_id": "job-1", "event": "leased",    "created_at": "2026-06-01T00:01:00Z"},
        {"id": 3, "job_id": "job-1", "event": "completed", "created_at": "2026-06-01T00:02:00Z"},
    ]
    return _make_mock_db(return_data=events)


# ── record_event ───────────────────────────────────────────────────────

class TestRecordEvent:
    """record_event(db_client, job_id, event, actor_id, actor_role, detail) -> bool"""

    def test_none_db_returns_false(self):
        """M-09-a: null db_client is rejected immediately."""
        assert record_event(None, "job-1", "created") is False

    def test_empty_job_id_returns_false(self):
        """M-09-b: empty or missing job_id is rejected."""
        mock = _make_mock_db()
        assert record_event(mock, "", "created") is False

    def test_none_job_id_returns_false(self):
        """M-09-c: None job_id is rejected."""
        mock = _make_mock_db()
        assert record_event(mock, None, "created") is False  # type: ignore[arg-type]

    def test_successful_insert_returns_true(self, mock_db):
        """M-09-d: valid input returns True on successful DB insert."""
        result = record_event(mock_db, "job-1", "created", actor_id="agent-1")
        assert result is True

    def test_insert_invokes_correct_table_and_fields(self, mock_db):
        """M-09-e: verifies the correct table and payload are sent."""
        record_event(mock_db, "job-x", "leased", actor_id="a1", actor_role="codex",
                     detail={"reason": "retry"})

        # Hash-chaining reads the chain's tail before appending, so a write now
        # touches the table twice (one select, one insert) - both on EVENTS_TABLE.
        assert mock_db.table.call_count == 2
        assert {c.args[0] for c in mock_db.table.call_args_list} == {EVENTS_TABLE}
        insert_payload = mock_db.table.return_value.insert.call_args[0][0]
        assert insert_payload["job_id"] == "job-x"
        assert insert_payload["event"] == "leased"
        assert insert_payload["actor_id"] == "a1"
        assert insert_payload["actor_role"] == "codex"
        assert insert_payload["detail"] == {"reason": "retry"}

    def test_db_exception_propagates_and_logs(self, caplog):
        mock = _make_mock_db(fail_on_insert=True)
        with pytest.raises(Exception, match="DB unavailable"):
            record_event(mock, "j1", "created")
        assert "Audit write failed" in caplog.text

    def test_detail_defaults_to_empty_dict(self, mock_db):
        """M-09-g: detail is stored as {} when not provided."""
        record_event(mock_db, "job-1", "canceled", actor_id="system")
        insert_payload = mock_db.table.return_value.insert.call_args[0][0]
        assert insert_payload["detail"] == {}


# ── get_events ─────────────────────────────────────────────────────────

class TestGetEvents:
    """get_events(db_client, job_id) -> list"""

    def test_none_db_returns_empty_list(self):
        """M-09-h: null db_client returns []."""
        assert get_events(None, "job-1") == []

    def test_returns_events_oldest_first(self, mock_db_with_events):
        """M-09-i: events are returned in chronological order."""
        events = get_events(mock_db_with_events, "job-1")
        timestamps = [e["created_at"] for e in events]
        assert timestamps == sorted(timestamps)

    def test_empty_job_id_still_queries(self, mock_db):
        """M-09-j: empty job_id still reaches the DB (filtered server-side)."""
        get_events(mock_db, "")
        mock_db.table.assert_called_once_with(EVENTS_TABLE)

    def test_db_exception_does_not_report_an_empty_valid_chain(self, caplog):
        """M-09-k: DB exception is caught, logged, and returns []."""
        mock = _make_mock_db(return_data=None)
        mock.table.return_value.select.side_effect = Exception("Query failed")
        import logging
        caplog.set_level(logging.ERROR)
        with pytest.raises(Exception, match="Query failed"):
            get_events(mock, "job-1")
        assert "Error fetching audit events" in caplog.text
def test_postgrest_fractional_timestamp_preserves_hash_but_not_tamper():
    from mco.orchestrator.audit import _content_of, _canonical, compute_hash
    original = {'job_id':'j','event':'created','actor_id':None,'actor_role':None,
                'detail':{'original':True},'created_at':'2026-09-05T05:44:45.898450+00:00'}
    stored = {**original,'created_at':'2026-09-05T05:44:45.89845+00:00','canonical_content':_canonical(original)}
    assert compute_hash('',_content_of(stored)) == compute_hash('',original)
    stored['detail'] = {'tampered':True}
    assert compute_hash('',_content_of(stored)) != compute_hash('',original)
