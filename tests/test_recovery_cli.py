from typer.testing import CliRunner

from mco import cli
from mco.localstore import LocalStore
from mco.orchestrator import audit, leases, routes
from tests.test_admin_routes import FakeConfig


def test_restore_command_pauses_and_invalidates_old_attempt(tmp_path, monkeypatch):
    db = LocalStore(tmp_path/'restore.db')
    config = FakeConfig()
    monkeypatch.setattr(routes,'get_db_client',lambda:db)
    monkeypatch.setattr(cli,'get_config',lambda:config)
    db.table('agent_jobs').insert({'id':'job','title':'restore','status':'pending'}).execute()
    claim = leases.acquire_lease(db,'job','worker')
    result = CliRunner().invoke(cli.app,['restore-fence'])
    assert result.exit_code == 0, result.output
    assert config.get('MCO_KILL_SWITCH') == 'true'
    assert not leases.renew_lease(db,claim)
    assert leases.store_incarnation(db) != claim.incarnation
    db.close()


def test_checkpoint_cli_refuses_overwrite_and_detects_missing_tail(tmp_path, monkeypatch):
    db = LocalStore(tmp_path/'checkpoint.db')
    monkeypatch.setattr(routes,'get_db_client',lambda:db)
    monkeypatch.setattr(audit,'_audit_hmac_key',lambda:b'test-signing-key')
    audit.record_event(db,'job','first')
    audit.record_event(db,'job','second')
    output = tmp_path/'checkpoint.json'
    runner = CliRunner()
    result = runner.invoke(cli.app,['audit-checkpoint','job',str(output)])
    assert result.exit_code == 0, result.output
    assert runner.invoke(cli.app,['audit-checkpoint','job',str(output)]).exit_code == 1
    tail = audit.get_events(db,'job')[-1]
    db._conn.execute('DELETE FROM agent_job_events WHERE pk=?',(tail['id'],))
    db._conn.commit()
    assert runner.invoke(cli.app,['audit','job','--checkpoint',str(output)]).exit_code == 1
    db.close()
