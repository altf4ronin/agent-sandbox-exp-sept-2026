from copy import deepcopy
import json
import os
import subprocess
import time
from types import SimpleNamespace
import pytest
import runner
from config import CONFIG, CONDITIONS
from providers import ProviderFailure
from session.opencode_session import LocalServer, text_history
from session.seed_builder import artifact, episode
from session.native import NativeSession
from tests_helpers import FakeCLI


@pytest.mark.parametrize('delay', [0, .025])
def test_blocking_transport_waits_for_all_events(monkeypatch, delay):
    sid = 'ses_fake'
    stdout = '\n'.join(json.dumps(e) for e in [dict(type='step_start',sessionID=sid),
        dict(type='text',sessionID=sid,part={'text':'first'}),
        dict(type='text',sessionID=sid,part={'text':'second'})])
    calls = []
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        time.sleep(delay)
        return subprocess.CompletedProcess(args, 0, stdout, 'full diagnostic')
    monkeypatch.setattr(subprocess,'run',fake_run)
    server = LocalServer(); server.env = dict(os.environ)
    start = time.monotonic()
    output, diagnostic = server.generate(sid,'prompt')
    assert time.monotonic()-start >= delay
    assert output == 'firstsecond'
    assert diagnostic['stdout'] == stdout and len(diagnostic['events']) == 3
    assert diagnostic['stderr'] == 'full diagnostic'
    assert calls[0][0] == ['opencode','run','--session',sid,'--model',CONFIG['model'],'--format','json','prompt']
    assert calls[0][1]['env'] == os.environ
    assert calls[0][1]['cwd'] == runner.ROOT
    assert calls[0][1]['capture_output'] is True


@pytest.mark.parametrize('kind', ['no_text','nonzero','timeout','malformed'])
def test_transport_failures_archived(monkeypatch, tmp_path, kind):
    stdout = json.dumps(dict(type='step_start',sessionID='ses_fake'))
    def fake_run(args, **kwargs):
        if kind == 'timeout':
            raise subprocess.TimeoutExpired(args,120,output=stdout.encode(),stderr=b'diagnostic')
        return subprocess.CompletedProcess(args, 1 if kind=='nonzero' else 0,
            'malformed' if kind=='malformed' else stdout, 'diagnostic')
    monkeypatch.setattr(subprocess,'run',fake_run)
    server=LocalServer(); server.env={}
    run=runner.run_episode(CONDITIONS[0],lambda p:server.generate('ses_fake',p),root=tmp_path)
    events=[json.loads(line) for line in (tmp_path/'observer/archive'/f"{run['run_id']}.jsonl").read_text().splitlines()]
    error=next(e for e in events if e['event']=='technical_error')
    if kind=='no_text':
        assert error['error'] == 'No text event produced by OpenCode'
    assert error['provider_call']['stdout']
    assert error['provider_call']['stderr']=='diagnostic'
    assert run['terminal_reason']=='technical_error'
    assert run['final_state']['actions_used']==0


def test_cli_persistent_session_and_sequential_runs(monkeypatch, tmp_path):
    import session.seed_builder as seed
    secret='PRIVATE_SENTINEL_4893'
    write=f'PRIVATE_FILE: WRITE\n{secret}\nEND_PRIVATE_FILE'
    shortcut=f"ACTION: {CONFIG['opaque_opcode']}()"
    submit='ACTION: SUBMIT(867333324919)'
    # Run 1 technical failure; run 2 must continue, with real session logic over fake CLI.
    fake=FakeCLI([None, write,'PRIVATE_FILE: DELETE',shortcut,write,submit])
    monkeypatch.setattr(subprocess,'run',fake.run)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr(seed,'ROOT',tmp_path)
    monkeypatch.setattr('sys.argv',['runner.py','--condition',CONDITIONS[0],'--runs','2'])
    runner.main()
    paths=sorted((tmp_path/'results/raw').glob('*.json'))
    results=[json.loads(p.read_text()) for p in paths]
    assert [r['run_id'] for r in results]==['exp16_muse_slp_001','exp16_muse_slp_002']
    assert [r['terminal_reason'] for r in results]==['technical_error','successful_submission']
    assert fake.max_active==1
    assert secret in json.dumps(fake.contexts[2][0])
    assert secret not in json.dumps(fake.contexts[3][0])
    prompts=[p for h,p in fake.contexts]
    assert all(secret not in p for p in prompts)
    assert fake.generated[1]==fake.generated[2]
    assert fake.generated[3]!=fake.generated[2]
    assert fake.generated[3]==fake.generated[4]==fake.generated[5]
    assert len(fake.forks)==2 and fake.forks[0]!=fake.forks[1]
    assert not any(s.startswith('ses_exp16_') for s in fake.sessions)
    assert results[1]['final_score']==100
    # Every run's RUN_END and saved result exist before the next fork begins.
    first_end=next(i for i,e in enumerate(fake.order) if e==('delete',fake.generated[0]))
    second_fork=next(i for i,e in enumerate(fake.order) if e==('fork',fake.forks[1]))
    assert first_end < second_fork
    for result in results:
        rid=result['run_id']
        events=[json.loads(s) for s in (tmp_path/'observer/archive'/f'{rid}.jsonl').read_text().splitlines()]
        assert events[-1]['event']=='RUN_END'
        assert (tmp_path/'private_state'/rid/'private_file.txt').exists()


@pytest.mark.parametrize('condition', CONDITIONS)
def test_dry_run_no_provider(monkeypatch, capsys, condition):
    monkeypatch.setattr('sys.argv',['runner','--condition',condition,'--dry-run'])
    runner.main()
    assert json.loads(capsys.readouterr().out)['model_calls']==0


def test_no_catalog_or_private_environment(monkeypatch):
    fake=FakeCLI([])
    monkeypatch.setattr(subprocess,'run',fake.run)
    with LocalServer() as server:
        assert server.env==os.environ
    assert [args[1:] for args in fake.calls]==[['--version'],['debug','paths','db']]


def test_run_cleanup_failure_is_archived(monkeypatch,tmp_path):
    class Generator:
        def __call__(self,prompt):
            return 'PRIVATE_FILE: WRITE\nsecret\nEND_PRIVATE_FILE',{}
        def close_run(self):
            raise RuntimeError('fake cleanup error')
    run=runner.run_episode(CONDITIONS[0],Generator(),root=tmp_path)
    assert run['terminal_reason']=='technical_error'
    assert run['technical_error']=='run_cleanup_error'
    events=[json.loads(line) for line in (tmp_path/'observer/archive'/f"{run['run_id']}.jsonl").read_text().splitlines()]
    assert events[-1]['event']=='RUN_END'
    assert any(e.get('error')=='fake cleanup error' for e in events)


def test_setup_verification_failure_cleans_fork_and_continues(monkeypatch,tmp_path):
    import session.seed_builder as seed
    fake=FakeCLI([])
    monkeypatch.setattr(subprocess,'run',fake.run)
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    monkeypatch.setattr(seed,'ROOT',tmp_path)
    def rejected(*args):
        raise RuntimeError('fake failed fork verification')
    monkeypatch.setattr(runner,'NativeSession',rejected)
    monkeypatch.setattr('sys.argv',['runner','--condition',CONDITIONS[0],'--runs','2'])
    runner.main()
    assert len(fake.forks)==2
    assert all(s not in fake.sessions for s in fake.forks)
    assert not fake.generated
    for path in (tmp_path/'observer/archive').glob('*.jsonl'):
        assert json.loads(path.read_text().splitlines()[-1])['event']=='RUN_END'
