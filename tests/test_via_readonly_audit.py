"""Offline safety and evidence tests: never call AWS or a public endpoint."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from mco.orchestrator import via_readonly_audit as audit


class Response:
    def __init__(self, url, body):
        self.url, self.body = url, json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body[:size]


def test_default_collect_only_fixed_read_operations(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        assert kwargs['shell'] is False and kwargs['timeout'] == 60
        assert argv[-7:] == ['--profile', audit.PROFILE, '--region', audit.REGION, '--output', 'json', '--no-cli-pager']
        body = {'Arn': 'arn:aws:iam::314086896527:user/auditor'} if argv[1] == 'sts' else {}
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr='')

    def opener(request, timeout):
        assert timeout == 30 and request.full_url in audit.URLS.values()
        return Response(request.full_url, {'status': 'ok'} if request.full_url.endswith('/health') else [])

    report = audit.collect(collector=audit.Collector(runner=runner, opener=opener))
    assert [tuple(c[1:-7]) for c in calls] == list(audit.COMMANDS.values())
    assert not any('send-command' in c for c in calls)
    assert report['production_readiness'] == 'not_determined'
    assert 'database_migration_head' in report['unknowns']
    path = audit.write_report(report, tmp_path)
    assert path.parent == tmp_path.resolve()
    assert '314086896527' not in path.read_text()


def test_reject_arbitrary_commands_and_urls():
    collector = audit.Collector(runner=lambda *a, **kw: pytest.fail('must not execute'))
    with pytest.raises(ValueError):
        collector.aws('restart')
    with pytest.raises(ValueError):
        collector.public('https://evil.example')


@pytest.mark.parametrize('mode', ['denied', 'timeout', 'bad_json'])
def test_failures_are_explicit_without_stderr(mode):
    def runner(*args, **kwargs):
        if mode == 'timeout':
            raise subprocess.TimeoutExpired('aws', 60)
        return SimpleNamespace(returncode=1 if mode == 'denied' else 0,
                               stdout='not-json', stderr='AccessDenied token=private 314086896527 10.0.0.1')
    result = audit.Collector(runner=runner).aws('identity')
    assert result['status'] == ('denied' if mode == 'denied' else 'failed')
    assert 'private' not in json.dumps(result)
    assert 'checked_at' in result


def test_real_api_shape_counts_confession_and_freshness():
    body = [{'occurrences': [{'service': 'confession', 'checkedAt': '2026-09-14T00:00:00Z'}, {'service': 'mass'}],
             'schedules': [{'service': 'mass'}], 'sources': [{'last_checked_at': '2026-09-13T00:00:00Z'}]},
            {'occurrences': [], 'schedules': [{'service': 'mass'}]}]
    result = audit.public_summary('lorain_confession', body)
    assert result['church_count'] == 2
    assert result['confession_occurrence_count'] == 1
    assert result['churches_with_confession_occurrences'] == 1
    assert len(result['freshness_observations']) == 2


def test_unknown_health_schema_and_redirect_are_gaps():
    for url, body in [(audit.URLS['public_health'], {}), ('https://other.example', {'status': 'ok'})]:
        collector = audit.Collector(opener=lambda *a, **kw: Response(url, body))
        assert collector.public('public_health')['status'] == 'failed'


def test_diagnostic_only_sends_constant_and_filters_output():
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if 'send-command' in argv:
            assert json.loads(argv[argv.index('--parameters') + 1]) == {'commands': [audit.DIAGNOSTIC]}
            body = {'Command': {'CommandId': '12345678-1234-1234-1234-123456789abc'}}
        else:
            body = {'Status': 'Success', 'StandardOutputContent': json.dumps({'deployment': {'commit': 'abc', 'secret': 'hidden'}, 'containers': 'via', 'current_link': '/opt/via/releases/abc', 'backup_timer': 'active', 'environment': 'hidden'}), 'StandardErrorContent': 'hidden'}
        return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr='')
    result = audit.Collector(runner=runner).diagnostic()
    assert result['status'] == 'success'
    assert 'hidden' not in json.dumps(result)
    assert len(calls) == 2
    for forbidden in ('restart', 'docker inspect', 'printenv', 'getenv', 'install', 'start-backup'):
        assert forbidden not in audit.DIAGNOSTIC


def test_sanitizer_and_backup_boundary():
    safe = audit.sanitize({'token': 'secret', 'note': '314086896527 10.0.0.1 password=abc'})
    assert safe['token'] == '[redacted]'
    assert 'abc' not in safe['note'] and '314086896527' not in safe['note']
    backup = audit.summarize('backups', {'Contents': [{'LastModified': '2026-09-13', 'Size': 2}, {'LastModified': '2026-09-14', 'Size': 3}], 'NextToken': 'secret'})
    assert backup['listing_truncated'] is True
    assert backup['latest_inspected_backup_bytes'] == 3
    assert backup['restore_verified'] is False
